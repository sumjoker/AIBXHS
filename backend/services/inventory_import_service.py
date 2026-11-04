#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
库存导入服务 - 后台异步执行
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from datetime import date
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import text
import pandas as pd
import numpy as np
from utils.excel_reader import safe_read_excel

# ---------------------------------------------------------------------------
# 导入状态：落盘为 JSON，让「子进程写 / 主进程读」跨进程可见。
#
# 导入本身跑在独立子进程（services/import_worker.py）中：子进程退出后由操作系统
# 无条件回收全部 RSS，从根本上消除「uvicorn worker 进程内导完内存长期不回落」。
# 内存画像见 scripts/measure_import_memory.py。
# ---------------------------------------------------------------------------
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STATUS_FILE = os.path.join(_BACKEND_DIR, ".import_status.json")

_DEFAULT_STATUS = {
    "is_running": False,
    "progress": 0,
    "step": "",
    "total_rows": 0,
    "inbound_count": 0,
    "replen_count": 0,
    "error": None,
    "started_at": None,
    "finished_at": None,
    "worker_pid": None,
}


def _read_status_file() -> dict:
    """读取状态文件，缺失字段用默认值补齐。"""
    try:
        with open(_STATUS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("状态文件格式异常")
    except Exception:
        data = {}
    for key, value in _DEFAULT_STATUS.items():
        data.setdefault(key, value)
    return data


def _write_status_file(status: dict) -> None:
    """原子写状态文件（tmp + replace，避免读到半个 JSON）。"""
    try:
        tmp = f"{_STATUS_FILE}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False)
        os.replace(tmp, _STATUS_FILE)
    except Exception:
        pass  # 状态落盘失败不得中断导入


class _StatusStore(dict):
    """写时落盘的状态字典：任何变更立即原子写 JSON，供其他进程读取。"""

    def __init__(self, path: str, initial: dict):
        super().__init__(initial)
        self._path = path

    def _flush(self):
        try:
            tmp = f"{self._path}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(dict(self), f, ensure_ascii=False)
            os.replace(tmp, self._path)
        except Exception:
            pass

    def __setitem__(self, key, value):
        if self.get(key) != value:  # 去重：补货计算的进度回调写入非常频繁
            super().__setitem__(key, value)
            self._flush()

    def update(self, *args, **kwargs):
        if args:
            super().update(*args)
        if kwargs:
            super().update(**kwargs)
        self._flush()


def _pid_alive(pid) -> bool:
    """判断进程是否存活。

    注意：Windows 下不能用 os.kill(pid, 0) —— CPython 的实现会退化为
    TerminateProcess，直接把目标进程杀掉。这里改用 OpenProcess 查询。
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False

    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


# 子进程内的权威状态（导入时由 _do_import 写入，每次变更自动落盘）
_import_status = _StatusStore(_STATUS_FILE, _read_status_file())


def get_import_status() -> dict:
    """获取导入状态（跨进程：读状态文件）"""
    status = _read_status_file()
    # 子进程被强杀时会残留 is_running=True，这里兜底修正
    if status.get("is_running") and not _pid_alive(status.get("worker_pid")):
        status["is_running"] = False
        status["error"] = status.get("error") or "导入进程已退出（异常终止）"
        status["finished_at"] = status.get("finished_at") or time.time()
        _write_status_file(status)
    return status


def _update_status(**kwargs):
    """更新导入状态"""
    _import_status.update(kwargs)


def _log(msg: str):
    """打印日志 - 强制刷新确保实时显示"""
    elapsed = time.time() - _import_status["started_at"] if _import_status["started_at"] else 0
    log_line = f"[导入 {elapsed:>6.1f}s] {msg}"
    print(log_line, flush=True)
    sys.stdout.flush()
    _import_status["step"] = msg


def _wait_sync_finish(wait_start: float = 30.0, timeout: float = 600.0, interval: float = 2.0) -> None:
    """等待飞书同步线程结束。

    start_sync_async() 起的是 daemon 线程，子进程一旦退出就会被强杀，
    因此子进程模式必须显式等待（含一个起步检测窗口，避免同步线程还没起跑就退出）。
    """
    from services.feishu_sync_service import get_sync_status

    t0 = time.time()
    while time.time() - t0 < wait_start:
        if get_sync_status().get("is_running"):
            break
        time.sleep(0.5)
    else:
        return  # 起步窗口内未启动，视为无同步任务

    while time.time() - t0 < timeout:
        if not get_sync_status().get("is_running"):
            print(f"[导入] FBA同步已结束（{time.time() - t0:.0f}s）", flush=True)
            return
        time.sleep(interval)
    print(f"[导入] 等待 FBA 同步超时（>{timeout:.0f}s），子进程退出", flush=True)


def _do_import(file_path: str = None, file_content: bytes = None, filename: str = None,
               tenant_id: int = 1, wait_for_sync: bool = False):
    """后台导入任务（在独立子进程中执行，见 services/import_worker.py）"""
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
            df = safe_read_excel(file_content)
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
        df["tenant_id"] = tenant_id
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
                WHERE tenant_id = :tid
                ORDER BY snapshot_date DESC
            """), {"tid": tenant_id}).fetchall()
        except Exception:
            existing_rows = []

        idx_normal = {}   # 普通行: (asin, account, country) → (id, snapshot_date)
        idx_summary = {}  # 汇总行: (summary_flag, asin, country) → (id, snapshot_date)

        for row in existing_rows:
            sf = row.summary_flag or ""
            if sf == "是":
                # 汇总行只进 idx_summary，用 (summary_flag, asin, country) 三元组做唯一键
                key_s = (sf, row.asin or "", row.country or "")
                if key_s not in idx_summary:
                    idx_summary[key_s] = (row.id, row.snapshot_date)
            else:
                key_n = (row.asin or "", row.account or "", row.country or "")
                if key_n not in idx_normal:
                    idx_normal[key_n] = (row.id, row.snapshot_date)

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
                    match_key = (row_summary_flag, row_asin, row_country)
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
                    update_batch.append(f"UPDATE inventory_snapshots SET deleted_at=NULL,updated_at=NOW(),{','.join(set_parts)} WHERE id={existing_id}")
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
                result_obj = db.execute(text(sql))
                first_id = result_obj.lastrowid
                db.commit()
                # lastrowid 是该 INSERT 产生的第一个自增 id，后续连续递增
                # batch_ids 中 None 的出现顺序与 INSERT VALUES 顺序一致（从前往后）
                if first_id:
                    cur_id = first_id
                    for j in range(len(batch_ids)):
                        if batch_ids[j] is None:
                            batch_ids[j] = cur_id
                            cur_id += 1
            
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
                            "tenant_id": tenant_id,
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
            # 防御性过滤：跳过 snapshot_id 为 None 的记录（避免 SQL "Unknown column 'None'"）
            _before = len(inbound_records)
            inbound_records = [r for r in inbound_records if r.get('snapshot_id') is not None]
            if len(inbound_records) < _before:
                _log(f"过滤掉 snapshot_id 为 None 的在途记录: {_before - len(inbound_records)} 条")

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

        # ========== 步骤6.1: 清理「无在途文本」快照下的历史残留明细 ==========
        # 某行从"有在途文本"变为"无文本"时，快照是原地 UPSERT（id 不变），
        # 上面的孤儿清理只覆盖本批有明细写入的快照，旧明细会永久残留。
        # 这里按「本次导入的租户 + 快照日期」做一次集合式清理，与按 key 比对的清理互补。
        cleanup_result = db.execute(text("""
            UPDATE inbound_shipment_details d
            JOIN inventory_snapshots s ON s.id = d.snapshot_id
            SET d.deleted_at = NOW()
            WHERE s.tenant_id = :tid
              AND s.snapshot_date = :sdate
              AND s.deleted_at IS NULL
              AND d.deleted_at IS NULL
              AND (s.fba_inbound_detail IS NULL OR TRIM(s.fba_inbound_detail) IN ('', 'nan'))
        """), {"tid": tenant_id, "sdate": today})
        db.commit()
        if cleanup_result.rowcount:
            _log(f"无在途文本快照的残留明细清理: 软删除 {cleanup_result.rowcount} 条")
        
        _import_status["progress"] = 70
        
        # ========== 步骤7: 孤儿清理（软删除） ==========
        _log("孤儿清理...")
        orphan_ids = set()
        imported_normal_keys = {(str(r.get("asin","")), str(r.get("account","")), str(r.get("country","")))
                                for _, r in df.iterrows() if str(r.get("summary_flag","")) != "是"}
        imported_summary_keys = {("是", str(r.get("asin","")), str(r.get("country","")))
                                 for _, r in df.iterrows() if str(r.get("summary_flag","")) == "是"}
        # 普通行：DB 有但 Excel 没有 → 孤儿
        for key, val in idx_normal.items():
            if key not in imported_normal_keys:
                orphan_ids.add(val[0])
        # 汇总行：DB 有但 Excel 没有 → 孤儿
        for key, val in idx_summary.items():
            if key not in imported_summary_keys:
                orphan_ids.add(val[0])

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
        # 注意：必须调用 calculate_replenishment（非 _calculate_replenishment_fast），
        # 因为前者会重新查库 + 重算日均销量 + 回写 daily_sales，保证与手动点击"重新计算"结果一致。
        # 直接用 _calculate_replenishment_fast 会导致 daily_sales 不准、全绿无补货建议。
        _log("计算补货决策 replenishment_decisions (重算日均销量 + UPSERT)...")
        from services.inventory_service import calculate_replenishment

        # 适配进度回调：将计算进度映射到 80-100 区间
        def _calc_progress(phase: str, current: int, total: int):
            pct = min(int(current / total * 100) if total > 0 else 0, 100)
            _import_status["progress"] = 80 + int(pct * 0.2)
            _import_status["step"] = f"补货计算: {phase}({pct}%)"

        calc_result = calculate_replenishment(
            db,
            snapshot_date=today.isoformat(),
            snapshot_ids=None,  # 不限制 snapshot_ids，计算当天全部快照
            progress_callback=_calc_progress,
            tenant_id=tenant_id
        )
        _import_status["replen_count"] = calc_result.get("total", 0)
        _log(f"replenishment_decisions 计算完成: {calc_result}")
        _import_status["progress"] = 95

        # ========== 步骤9（已后移）: FBA同步在途 ==========
        # 见函数末尾：必须在 db.close() 之后触发，先释放 Session 持有的 ORM 对象。
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
        try:
            db.close()  # 先释放 Session 持有的全部 ORM 对象（46790 个）
        except Exception:
            pass

    # ========== 步骤9: 触发FBA同步在途 ==========
    # 必须放在 db.close() 之后：先让 Session 释放 ORM 对象，再去做同步。
    if not _import_status.get("error"):
        _log("触发FBA同步在途...")
        try:
            from services.feishu_sync_service import start_sync_async as start_feishu_sync
            sync_result = start_feishu_sync()
            if sync_result.get("started"):
                _log(f"FBA同步在途已启动: {sync_result.get('message', '')}")
            else:
                _log(f"FBA同步在途未启动: {sync_result.get('message', '')}")
        except Exception as sync_e:
            _log(f"FBA同步在途触发失败（不影响导入结果）: {sync_e}")
        if wait_for_sync:
            _wait_sync_finish()


_start_lock = threading.Lock()


def _reap_worker(proc) -> None:
    """等待子进程结束并回收；子进程异常退出时兜底修正残留状态。"""
    try:
        proc.wait()
    except Exception:
        return
    status = _read_status_file()
    if status.get("is_running"):
        status["is_running"] = False
        status["error"] = status.get("error") or f"导入进程异常退出（退出码 {proc.returncode}）"
        status["finished_at"] = status.get("finished_at") or time.time()
        _write_status_file(status)


def start_import_async(file_path: str = None, file_content: bytes = None, filename: str = None, tenant_id: int = 1) -> dict:
    """启动异步导入任务（独立子进程执行）。

    子进程退出后由操作系统无条件回收全部 RSS —— 这是「导入后内存长期不回落」
    的结构性解法（allocator 高水位 / ORM 对象 / 编译缓存都随进程一起消失）。
    """
    with _start_lock:
        current = get_import_status()
        if current.get("is_running"):
            return {"started": False, "message": "导入任务正在运行中", "status": current}

        # 上传内容先落盘，交给子进程按路径读取（避免跨进程传 10MB+ bytes）
        tmp_file = None
        if file_content is not None:
            fd, tmp_file = tempfile.mkstemp(prefix="restock_import_", suffix=".xlsx")
            with os.fdopen(fd, "wb") as fh:
                fh.write(file_content)
            file_path = tmp_file

        if not file_path or not os.path.exists(file_path):
            if tmp_file and os.path.exists(tmp_file):
                try:
                    os.remove(tmp_file)
                except OSError:
                    pass
            return {"started": False, "message": f"文件不存在: {file_path}", "status": current}

        display_name = filename or os.path.basename(file_path)

        # 初始状态落盘（子进程启动后会接管）
        _write_status_file({**_DEFAULT_STATUS,
            "is_running": True,
            "step": "正在启动导入进程...",
            "started_at": time.time(),
        })

        cmd = [
            sys.executable, "-m", "services.import_worker",
            "--file", file_path,
            "--tenant", str(tenant_id),
            "--filename", display_name,
        ]
        if tmp_file:
            cmd += ["--cleanup", tmp_file]

        env = dict(os.environ)
        env["PYTHONPATH"] = _BACKEND_DIR + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONIOENCODING"] = "utf-8"

        try:
            proc = subprocess.Popen(cmd, cwd=_BACKEND_DIR, env=env)
        except Exception as e:
            if tmp_file and os.path.exists(tmp_file):
                try:
                    os.remove(tmp_file)
                except OSError:
                    pass
            _write_status_file({**_DEFAULT_STATUS,
                "error": f"启动导入进程失败: {e}",
                "finished_at": time.time(),
            })
            return {"started": False, "message": f"启动导入进程失败: {e}"}

        status = _read_status_file()
        status["worker_pid"] = proc.pid
        _write_status_file(status)

        # 后台回收子进程（只做 wait，不加载任何数据）
        threading.Thread(target=_reap_worker, args=(proc,), daemon=True).start()

        return {"started": True, "message": "导入任务已启动", "pid": proc.pid}
