#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
库存导入服务 - 后台异步执行
"""

import threading
import time
from datetime import date
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import text
import pandas as pd
import numpy as np

# 导入状态（内存存储）
_import_status = {
    "is_running": False,
    "progress": 0,
    "step": "",
    "total_rows": 0,
    "inbound_count": 0,
    "replen_count": 0,
    "error": None,
    "started_at": None,
    "finished_at": None,
}


def get_import_status() -> dict:
    """获取导入状态"""
    return _import_status.copy()


def _update_status(**kwargs):
    """更新导入状态"""
    _import_status.update(kwargs)


def _log(msg: str):
    """打印日志 - 强制刷新确保实时显示"""
    import sys
    elapsed = time.time() - _import_status["started_at"] if _import_status["started_at"] else 0
    log_line = f"[导入 {elapsed:>6.1f}s] {msg}"
    print(log_line, flush=True)
    sys.stdout.flush()
    _import_status["step"] = msg


def _do_import(file_path: str = None, file_content: bytes = None, filename: str = None):
    """后台导入任务"""
    global _import_status
    
    from database.database import SessionLocal, engine
    from services.inventory_service import (
        FIELD_MAPPING, LEAD_TIME, NUMERIC_FIELDS,
        _calculate_daily_sales, _parse_inbound_details_fast, _calculate_replenishment_fast
    )
    import io
    
    db = SessionLocal()
    _t_start = time.time()
    
    try:
        # 初始化状态
        _import_status.update({
            "is_running": True,
            "progress": 0,
            "step": "开始导入...",
            "total_rows": 0,
            "inbound_count": 0,
            "replen_count": 0,
            "error": None,
            "started_at": _t_start,
            "finished_at": None,
        })
        
        # ========== 步骤1: 读取Excel ==========
        _log(f"读取Excel: {filename or file_path or '上传文件'}")
        if file_content:
            df = pd.read_excel(io.BytesIO(file_content))
        elif file_path:
            df = pd.read_excel(file_path)
        else:
            raise ValueError("请提供 file_path 或 file_content")
        
        today = date.today()
        total_rows = len(df)
        _import_status["total_rows"] = total_rows
        _log(f"Excel读取完成: {total_rows} 行, {len(df.columns)} 列")
        _import_status["progress"] = 5
        
        # 重命名列
        df = df.rename(columns={k: v for k, v in FIELD_MAPPING.items() if k in df.columns})
        
        # 数据清洗
        _log("数据清洗中...")
        if "summary_flag" in df.columns:
            df["summary_flag"] = df["summary_flag"].fillna("0").apply(
                lambda x: "0" if str(x).strip() in ("", "0") else str(x).strip()
            )
        
        for col in NUMERIC_FIELDS:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        
        str_cols = ["asin", "parent_asin", "msku", "fnsku", "sku", "product_name", "title", 
                    "account", "country", "category", "brand", "fba_inbound_detail"]
        for col in str_cols:
            if col in df.columns:
                df[col] = df[col].fillna("").astype(str)
        
        df["snapshot_date"] = today
        df["tenant_id"] = 1
        _log("数据清洗完成")

        # 动态计算总库存：fba_stock + fba_inbound + local_inventory - inspection_quantity
        fba_stock_exists = "fba_stock" in df.columns
        fba_inbound_exists = "fba_inbound" in df.columns
        local_inv_exists = "local_inventory" in df.columns
        inspection_exists = "inspection_quantity" in df.columns
        if fba_stock_exists and fba_inbound_exists and local_inv_exists:
            inspection = df["inspection_quantity"].fillna(0) if inspection_exists else 0
            df["total_stock"] = (
                df["fba_stock"].fillna(0) +
                df["fba_inbound"].fillna(0) +
                df["local_inventory"].fillna(0) -
                inspection
            )
            _log(f"动态计算 total_stock 完成（覆盖Excel原始值）")
        _import_status["progress"] = 10
        
        # ========== 步骤2: 建立旧数据索引（按产品取最新的一条） ==========
        _log("加载现有数据索引...")
        try:
            existing_rows = db.execute(text("""
                SELECT id, asin, account, country, summary_flag, snapshot_date
                FROM inventory_snapshots
                ORDER BY snapshot_date DESC
            """)).fetchall()
        except Exception:
            existing_rows = []

        idx_normal = {}   # (asin, account, country) → (id, snapshot_date)
        idx_summary = {}  # (summary_flag, asin) → (id, snapshot_date)

        for row in existing_rows:
            key_n = (row.asin or "", row.account or "", row.country or "")
            key_s = (row.summary_flag or "", row.asin or "")
            # 同一产品取最新日期的那条（ORDER BY DESC 保证第一条就是最新的）
            if key_n not in idx_normal:
                idx_normal[key_n] = (row.id, row.snapshot_date)
            if key_s not in idx_summary:
                idx_summary[key_s] = (row.id, row.snapshot_date)

        _log(f"现有数据索引加载完成: {len(existing_rows)} 条原始, {len(idx_normal)} 个独立产品")
        
        # ========== 步骤3: UPSERT 快照 ==========
        _log(f"开始 UPSERT inventory_snapshots ({total_rows} 条)...")
        available_columns = [col for col in FIELD_MAPPING.values() if col in df.columns] + ["snapshot_date", "tenant_id"]
        df_to_insert = df[available_columns].copy()
        df_to_insert["stock_up_duration"] = LEAD_TIME

        _log("计算日均销量...")
        daily_sales_values = _calculate_daily_sales(df, db)
        df_to_insert["daily_sales"] = daily_sales_values

        columns = list(df_to_insert.columns)
        batch_size = 1000
        snapshot_ids = []  # 按 Excel 行序收集 id
        inserted_count = 0
        updated_count = 0

        for i in range(0, len(df_to_insert), batch_size):
            batch = df_to_insert.iloc[i:i+batch_size]
            update_batch = []
            insert_batch = []
            batch_ids = []
            
            for _, row in batch.iterrows():
                row_summary_flag = str(row.get("summary_flag", "0"))
                row_asin = str(row.get("asin", ""))
                row_account = str(row.get("account", ""))
                row_country = str(row.get("country", ""))
                
                if row_summary_flag == "是":
                    match_key = (row_summary_flag, row_asin)
                    existing = idx_summary.get(match_key)
                else:
                    match_key = (row_asin, row_account, row_country)
                    existing = idx_normal.get(match_key)
                
                if existing is not None:
                    existing_id = existing[0]  # 从 (id, snapshot_date) 元组中取 id
                    # UPDATE
                    set_parts = []
                    for col in columns:
                        val = row[col]
                        if pd.isna(val):
                            continue  # 跳过空值，保留数据库原值
                        elif isinstance(val, str):
                            safe = str(val).replace("'", "''")
                            set_parts.append(f"{col}='{safe}'")
                        elif isinstance(val, (int, float)):
                            set_parts.append(f"{col}={val}")
                        elif isinstance(val, date):
                            set_parts.append(f"{col}='{val}'")
                        else:
                            safe = str(val).replace("'", "''")
                            set_parts.append(f"{col}='{safe}'")
                    update_batch.append(f"UPDATE inventory_snapshots SET deleted_at=NULL,{','.join(set_parts)} WHERE id={existing_id}")
                    batch_ids.append(existing_id)
                    updated_count += 1
                else:
                    # INSERT
                    row_values = []
                    for col in columns:
                        val = row[col]
                        if pd.isna(val):
                            row_values.append("NULL")
                        elif isinstance(val, str):
                            row_values.append(f"'{str(val).replace(chr(39), chr(39)+chr(39))}'")
                        elif isinstance(val, (int, float)):
                            row_values.append(str(val))
                        elif isinstance(val, date):
                            row_values.append(f"'{val}'")
                        else:
                            row_values.append(f"'{str(val)}'")
                    insert_batch.append(f"({','.join(row_values)})")
                    batch_ids.append(None)  # 占位，INSERT 后获取 id
                    inserted_count += 1
            
            # 执行 UPDATE
            for sql in update_batch:
                db.execute(text(sql))
            db.commit()
            
            # 执行 INSERT 并获取 id
            if insert_batch:
                sql = f"INSERT INTO inventory_snapshots ({','.join(columns)}) VALUES {','.join(insert_batch)}"
                db.execute(text(sql))
                db.commit()
                # 获取最后一批 INSERT 的 id
                result = db.execute(text("SELECT LAST_INSERT_ID()")).scalar()
                if result:
                    last_id = result
                    for j in range(len(batch_ids) - 1, -1, -1):
                        if batch_ids[j] is None:
                            batch_ids[j] = last_id
                            last_id -= 1
            
            snapshot_ids.extend(batch_ids)
            
            done = min(i + batch_size, len(df_to_insert))
            progress = 15 + int(done / len(df_to_insert) * 20)
            _import_status["progress"] = progress
            if done % 5000 == 0 or done >= len(df_to_insert):
                _log(f"  UPSERT进度: {done}/{len(df_to_insert)}, 更新{updated_count} 新增{inserted_count}")

        _log(f"inventory_snapshots UPSERT完成: {total_rows} 条 (更新{updated_count} 新增{inserted_count})")
        _import_status["progress"] = 35
        
        # ========== 步骤4: 确认快照ID ==========
        _log(f"确认快照ID: {len(snapshot_ids)} 个")
        assert len(snapshot_ids) == total_rows, f"snapshot_ids 数量 ({len(snapshot_ids)}) 与 Excel 行数 ({total_rows}) 不匹配"
        _import_status["progress"] = 40
        
        # ========== 步骤5: 解析在途详情 ==========
        _log("解析在途详情...")
        inbound_records = []
        df_reset = df.reset_index(drop=True)
        
        detail_count = 0
        for idx in range(len(df_reset)):
            raw_detail = df_reset.iloc[idx].get("fba_inbound_detail", "")
            if raw_detail and str(raw_detail).strip() and str(raw_detail).strip() != "nan":
                detail_count += 1
        _log(f"有在途详情的记录: {detail_count}/{total_rows} 条")
        
        for idx in range(len(df_reset)):
            row = df_reset.iloc[idx]
            raw_detail = row.get("fba_inbound_detail", "")
            if raw_detail and str(raw_detail).strip() and str(raw_detail).strip() != "nan":
                if idx < len(snapshot_ids):
                    details = _parse_inbound_details_fast(raw_detail)
                    for d in details:
                        inbound_records.append({
                            "tenant_id": 1,
                            "snapshot_id": snapshot_ids[idx],
                            "asin": row.get("asin", ""),
                            "account": row.get("account", ""),
                            "country": row.get("country", ""),
                            **d
                        })
            
            if (idx + 1) % 10000 == 0:
                progress = 40 + int((idx + 1) / total_rows * 10)
                _import_status["progress"] = progress
                _log(f"  解析进度: {idx+1}/{total_rows}, 在途详情: {len(inbound_records)} 条")
        
        _import_status["inbound_count"] = len(inbound_records)
        _log(f"在途详情解析完成: {len(inbound_records)} 条")
        _import_status["progress"] = 50
        
        # ========== 步骤6: 导入在途详情 (UPSERT) ==========
        if inbound_records:
            _log(f"开始 UPSERT inbound_shipment_details ({len(inbound_records)} 条)...")
            
            # 加载当前批次涉及的在途详情现有记录（含软删），建立索引
            all_sids = set(r['snapshot_id'] for r in inbound_records)
            ids_str = ",".join(str(sid) for sid in all_sids)
            existing_inbound = db.execute(text(f"""
                SELECT id, snapshot_id, shipment_id, deleted_at
                FROM inbound_shipment_details
                WHERE snapshot_id IN ({ids_str})
            """)).fetchall()
            inbound_idx = {}
            inbound_deleted_idx = {}
            for row in existing_inbound:
                key = (row.snapshot_id, row.shipment_id)
                if row.deleted_at is None:
                    inbound_idx[key] = row.id
                elif key not in inbound_idx:
                    inbound_deleted_idx[key] = row.id
            
            matched_inbound_keys = set()
            batch_size = 1000
            updated_inbound = 0
            inserted_inbound = 0
            
            for i in range(0, len(inbound_records), batch_size):
                batch = inbound_records[i:i+batch_size]
                update_batch = []
                insert_values = []
                
                for r in batch:
                    key = (r['snapshot_id'], r['shipment_id'])
                    existing_id = inbound_idx.get(key)
                    is_deleted_restore = False
                    if existing_id is None:
                        existing_id = inbound_deleted_idx.get(key)
                        if existing_id is not None:
                            is_deleted_restore = True
                    
                    if existing_id is not None:
                        matched_inbound_keys.add(key)
                        # UPDATE — 跳过空值
                        set_parts = []
                        sid = r['snapshot_id']
                        asin = str(r.get('asin', '')).replace("'", "''")
                        account = str(r.get('account', '')).replace("'", "''")
                        country = str(r.get('country', '')).replace("'", "''")
                        shipment_id = str(r.get('shipment_id', '')).replace("'", "''")
                        if asin:
                            set_parts.append(f"asin='{asin}'")
                        if account:
                            set_parts.append(f"account='{account}'")
                        if country:
                            set_parts.append(f"country='{country}'")
                        if shipment_id:
                            set_parts.append(f"shipment_id='{shipment_id}'")
                        qty = r.get('quantity', 0) or 0
                        set_parts.append(f"quantity={qty}")
                        lm = str(r.get('logistics_method', '') or '')
                        if lm:
                            set_parts.append(f"logistics_method='{lm.replace(chr(39), chr(39)+chr(39))}'")
                        tm = str(r.get('transport_method', '') or '')
                        if tm:
                            set_parts.append(f"transport_method='{tm.replace(chr(39), chr(39)+chr(39))}'")
                        if r.get('ship_date'):
                            set_parts.append(f"ship_date='{r['ship_date']}'")
                        if r.get('estimated_available_date'):
                            set_parts.append(f"estimated_available_date='{r['estimated_available_date']}'")
                        raw = str(r.get('raw_text', '')).replace("'", "''")
                        if raw:
                            set_parts.append(f"raw_text='{raw}'")
                        
                        if set_parts:
                            if is_deleted_restore:
                                set_parts.insert(0, "deleted_at=NULL")
                            update_batch.append(f"UPDATE inbound_shipment_details SET {','.join(set_parts)} WHERE id={existing_id}")
                            updated_inbound += 1
                    else:
                        # INSERT
                        sid = r['snapshot_id']
                        a = str(r.get('asin', '')).replace("'", "''")
                        acc = str(r.get('account', '')).replace("'", "''")
                        cntry = str(r.get('country', '')).replace("'", "''")
                        ship = str(r.get('shipment_id', '')).replace("'", "''")
                        qty = r.get('quantity', 0) or 0
                        lm = str(r.get('logistics_method', '') or '').replace("'", "''")
                        tm = str(r.get('transport_method', '') or '').replace("'", "''")
                        sd = f"'{r['ship_date']}'" if r.get('ship_date') else "NULL"
                        ed = f"'{r['estimated_available_date']}'" if r.get('estimated_available_date') else "NULL"
                        raw = str(r.get('raw_text', '')).replace("'", "''")
                        insert_values.append(f"({r['tenant_id']}, {sid}, '{a}', '{acc}', '{cntry}', '{ship}', {qty}, '{lm}', '{tm}', {sd}, {ed}, '{raw}')")
                        inserted_inbound += 1
                
                for sql in update_batch:
                    db.execute(text(sql))
                db.commit()
                
                if insert_values:
                    sql = f"""
                        INSERT INTO inbound_shipment_details
                        (tenant_id, snapshot_id, asin, account, country, shipment_id, quantity, logistics_method, transport_method, ship_date, estimated_available_date, raw_text)
                        VALUES {','.join(insert_values)}
                    """
                    db.execute(text(sql))
                    db.commit()
                
                done = min(i + batch_size, len(inbound_records))
                progress = 50 + int(done / len(inbound_records) * 20)
                _import_status["progress"] = progress
                if done % 5000 == 0 or done >= len(inbound_records):
                    _log(f"  UPSERT进度: {done}/{len(inbound_records)}, 更新{updated_inbound} 新增{inserted_inbound}")
            
            # 孤儿清理：DB有但Excel没有 → 软删除
            orphan_inbound_ids = []
            for key, eid in inbound_idx.items():
                if key not in matched_inbound_keys:
                    orphan_inbound_ids.append(eid)
            if orphan_inbound_ids:
                ids_str = ",".join(str(i) for i in orphan_inbound_ids)
                db.execute(text(f"UPDATE inbound_shipment_details SET deleted_at = NOW() WHERE id IN ({ids_str})"))
                db.commit()
                _log(f"  孤儿在途详情软删除: {len(orphan_inbound_ids)} 条")
            
            # fba_inbound 校验修正
            _log("fba_inbound 校验修正...")
            for sid in all_sids:
                sum_qty = db.execute(text(
                    f"SELECT COALESCE(SUM(quantity), 0) FROM inbound_shipment_details WHERE snapshot_id = {sid} AND deleted_at IS NULL"
                )).scalar() or 0
                old_val = db.execute(text(
                    f"SELECT fba_inbound FROM inventory_snapshots WHERE id = {sid} AND deleted_at IS NULL"
                )).scalar()
                if old_val is not None and old_val != sum_qty:
                    db.execute(text(
                        f"UPDATE inventory_snapshots SET fba_inbound = {sum_qty} WHERE id = {sid} AND deleted_at IS NULL"
                    ))
                    db.commit()
                    _log(f"  fba_inbound 校验修正: snapshot_id={sid}, {old_val} → {sum_qty}")
            
            _log(f"inbound_shipment_details UPSERT完成: 更新{updated_inbound} 新增{inserted_inbound}")
        else:
            _log("没有在途详情需要导入")
        
        _import_status["progress"] = 70
        
        # ========== 步骤7: 孤儿清理（软删除） ==========
        _log("孤儿清理...")
        orphan_ids = []
        imported_normal_keys = {(str(r.get("asin","")), str(r.get("account","")), str(r.get("country",""))) for _, r in df.iterrows()}
        for key, val in idx_normal.items():
            if key not in imported_normal_keys:
                orphan_ids.append(val[0])  # val 是 (id, snapshot_date)，取 id

        if orphan_ids:
            ids_str = ",".join(str(i) for i in orphan_ids)
            db.execute(text(f"UPDATE replenishment_decisions SET deleted_at = NOW() WHERE snapshot_id IN ({ids_str})"))
            db.execute(text(f"UPDATE inbound_shipment_details SET deleted_at = NOW() WHERE snapshot_id IN ({ids_str})"))
            db.execute(text(f"UPDATE inventory_snapshots SET deleted_at = NOW() WHERE id IN ({ids_str})"))
            db.commit()
            _log(f"孤儿清理完成: 软删除 {len(orphan_ids)} 条")
        else:
            _log("无孤儿数据需要清理")
        _import_status["progress"] = 72
        
        # ========== 步骤8: 计算补货决策（UPSERT） ==========
        _log("计算补货决策 replenishment_decisions (UPSERT)...")
        calc_result = _calculate_replenishment_fast(db, df, snapshot_ids, today)
        _import_status["replen_count"] = calc_result.get("total", 0)
        _log(f"replenishment_decisions 计算完成: {calc_result}")
        _import_status["progress"] = 100
        
        # ========== 完成 ==========
        total_time = time.time() - _t_start
        _log(f"全部完成! 总耗时 {total_time:.1f}s")
        print(f"\n{'='*60}")
        print(f"导入汇总:")
        print(f"  inventory_snapshots:      {total_rows} 条")
        print(f"  inbound_shipment_details: {len(inbound_records)} 条")
        print(f"  replenishment_decisions:  {calc_result.get('total', 'N/A')} 条")
        print(f"  总耗时: {total_time:.1f}s")
        print(f"{'='*60}")
        
        _import_status.update({
            "is_running": False,
            "finished_at": time.time(),
        })
        
    except Exception as e:
        _import_status.update({
            "is_running": False,
            "error": str(e),
            "finished_at": time.time(),
        })
        _log(f"导入失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()


def start_import_async(file_path: str = None, file_content: bytes = None, filename: str = None) -> dict:
    """启动异步导入任务"""
    global _import_status
    
    if _import_status["is_running"]:
        return {"started": False, "message": "导入任务正在运行中", "status": get_import_status()}
    
    # 重置状态
    _import_status = {
        "is_running": False,
        "progress": 0,
        "step": "",
        "total_rows": 0,
        "inbound_count": 0,
        "replen_count": 0,
        "error": None,
        "started_at": None,
        "finished_at": None,
    }
    
    # 启动后台线程
    thread = threading.Thread(
        target=_do_import,
        args=(file_path, file_content, filename),
        daemon=True
    )
    thread.start()
    
    return {"started": True, "message": "导入任务已启动"}
