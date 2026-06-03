#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
飞书多维表格同步服务 - 简化版
状态由前端管理，后端只提供当前状态查询
"""

import requests
from datetime import datetime, date, timedelta
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import text
import threading
import time
import concurrent.futures

# 飞书应用配置
FEISHU_APP_ID = "cli_a88206945560500c"
FEISHU_APP_SECRET = "zwTlhqW5d1dDkVZVEsGTxfOuUbpfBZVg"
FEISHU_APP_TOKEN = "N4DoblLB6a3nbKsmngrcQxc6nMh"
FEISHU_TABLE_ID = "tblDHtSEjlu5eK6l"

# 内存状态（仅当前进程有效）
_sync_status = {
    "is_running": False,
    "progress": 0,
    "total": 0,
    "updated": 0,
    "error": None,
    "finished_at": None,
    "step": ""
}


def get_feishu_token() -> Optional[str]:
    """获取飞书 tenant_access_token"""
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    resp = requests.post(url, json={
        "app_id": FEISHU_APP_ID,
        "app_secret": FEISHU_APP_SECRET
    }, timeout=10)
    result = resp.json()
    if result.get("code") == 0:
        return result["tenant_access_token"]
    return None


def fetch_single_page(token: str, page_token: Optional[str] = None) -> dict:
    """获取单页数据"""
    headers = {"Authorization": f"Bearer {token}"}
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}/tables/{FEISHU_TABLE_ID}/records"
    
    params = {"page_size": 500}
    if page_token:
        params["page_token"] = page_token
    
    resp = requests.get(url, headers=headers, params=params, timeout=30)
    return resp.json()


def fetch_feishu_inbound_data_fast(days: int = 90) -> list:
    """快速获取飞书数据 - 使用并发"""
    token = get_feishu_token()
    if not token:
        raise Exception("获取飞书token失败")
    
    cutoff_date = (datetime.now() - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff_timestamp = int(cutoff_date.timestamp() * 1000)
    
    all_records = []
    
    # 先获取第一页
    result = fetch_single_page(token)
    if result.get("code") != 0:
        raise Exception(f"获取飞书数据失败: {result.get('msg')}")
    
    # 处理第一页
    for item in result["data"]["items"]:
        record = parse_record(item, cutoff_timestamp)
        if record:
            all_records.append(record)
    
    # 获取所有page_token
    page_tokens = []
    page_token = result["data"].get("page_token")
    while page_token:
        page_tokens.append(page_token)
        temp_result = fetch_single_page(token, page_token)
        if not temp_result["data"].get("has_more"):
            break
        page_token = temp_result["data"].get("page_token")
        if len(page_tokens) >= 10:
            break
    
    # 并发获取剩余页面
    def fetch_page(pt):
        try:
            r = fetch_single_page(token, pt)
            if r.get("code") == 0:
                records = []
                for item in r["data"]["items"]:
                    rec = parse_record(item, cutoff_timestamp)
                    if rec:
                        records.append(rec)
                return records
        except Exception as e:
            print(f"获取页面失败: {e}")
        return []
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(fetch_page, pt) for pt in page_tokens]
        for future in concurrent.futures.as_completed(futures):
            records = future.result()
            all_records.extend(records)
    
    return all_records


def parse_record(item: dict, cutoff_timestamp: int) -> Optional[dict]:
    """解析单条记录"""
    fields = item["fields"]
    
    report_timestamp = fields.get("填报日期")
    if report_timestamp and report_timestamp < cutoff_timestamp:
        return None
    
    shipment_id = fields.get("FBA（仓库填）", "")
    if not shipment_id:
        return None
    
    transport_method = fields.get("渠道（仓库填）", "")
    
    eta_timestamp = fields.get("预计到港时间（仓库填）")
    estimated_date = None
    if eta_timestamp:
        try:
            estimated_date = datetime.fromtimestamp(eta_timestamp / 1000).date()
        except:
            pass
    
    return {
        "shipment_id": str(shipment_id),
        "transport_method": str(transport_method) if transport_method else "",
        "estimated_arrival_date": estimated_date
    }


def get_sync_status() -> dict:
    """获取当前同步状态"""
    return _sync_status.copy()


def _do_sync():
    """后台同步任务"""
    global _sync_status
    
    from database.database import SessionLocal
    db = SessionLocal()
    
    try:
        # 初始化状态
        _sync_status = {
            "is_running": True,
            "progress": 0,
            "total": 0,
            "updated": 0,
            "error": None,
            "finished_at": None,
            "step": "获取飞书数据..."
        }
        
        # 步骤1: 获取飞书数据
        t0 = time.time()
        feishu_data = fetch_feishu_inbound_data_fast(days=90)
        _sync_status["total"] = len(feishu_data)
        _sync_status["step"] = f"获取到 {len(feishu_data)} 条飞书数据"
        
        if not feishu_data:
            _sync_status["is_running"] = False
            _sync_status["step"] = "没有需要同步的数据"
            return
        
        # 步骤2: 批量UPDATE
        _sync_status["step"] = "更新数据库..."
        _sync_status["progress"] = 30
        
        batch_size = 500
        total_updated = 0
        
        for i in range(0, len(feishu_data), batch_size):
            batch = feishu_data[i:i + batch_size]
            
            transport_cases = []
            eta_cases = []
            shipment_ids = []
            
            for item in batch:
                sid = item["shipment_id"].replace("'", "''")
                tm = item["transport_method"].replace("'", "''")
                eta = f"'{item['estimated_arrival_date']}'" if item["estimated_arrival_date"] else "NULL"
                
                shipment_ids.append(f"'{sid}'")
                transport_cases.append(f"WHEN '{sid}' THEN '{tm}'")
                eta_cases.append(f"WHEN '{sid}' THEN {eta}")
            
            sql = f"""
                UPDATE inbound_shipment_details
                SET 
                    transport_method = CASE shipment_id {chr(10).join(transport_cases)} END,
                    estimated_arrival_date = CASE shipment_id {chr(10).join(eta_cases)} END,
                    updated_at = NOW()
                WHERE shipment_id IN ({','.join(shipment_ids)}) AND deleted_at IS NULL
            """
            result = db.execute(text(sql))
            db.commit()
            total_updated += result.rowcount
            
            progress = 30 + int((i + batch_size) / len(feishu_data) * 60)
            _sync_status["progress"] = min(progress, 90)
            _sync_status["step"] = f"更新数据库 {min(i + batch_size, len(feishu_data))}/{len(feishu_data)}"
        
        # 完成
        _sync_status["is_running"] = False
        _sync_status["updated"] = total_updated
        _sync_status["progress"] = 100
        _sync_status["step"] = f"同步完成！更新 {total_updated} 条记录"
        _sync_status["finished_at"] = datetime.now().isoformat()
        
    except Exception as e:
        _sync_status["is_running"] = False
        _sync_status["error"] = str(e)
        _sync_status["step"] = f"同步失败: {str(e)}"
        import traceback
        traceback.print_exc()
    finally:
        db.close()


def start_sync_async() -> dict:
    """启动异步同步任务"""
    if _sync_status["is_running"]:
        return {"started": False, "message": "同步任务正在运行中"}
    
    # 重置状态
    _sync_status.update({
        "is_running": False,
        "progress": 0,
        "total": 0,
        "updated": 0,
        "error": None,
        "finished_at": None,
        "step": ""
    })
    
    # 启动后台线程
    thread = threading.Thread(target=_do_sync, daemon=True)
    thread.start()
    
    return {"started": True, "message": "同步任务已启动"}
