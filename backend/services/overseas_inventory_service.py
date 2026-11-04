#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
海外仓库存同步服务 - 飞书多维表 → 数据库

飞书应用凭据共用 FBA 同步应用（FEISHU_SYNC_APP_ID / FEISHU_SYNC_APP_SECRET），
本表只需在 .env 配置 FEISHU_OVERSEAS_BASE_TOKEN / FEISHU_OVERSEAS_TABLE_ID。

一、同步规则（覆盖式，不追加历史）
    源表是「按日快照」结构：同一 (SKU, SKU名称) 每天产生一条记录，
    21251 条历史记录实际只有 499 个 SKU（每个 SKU 约 40+ 天的历史），
    因此：
      1. 去重口径 —— 以 (SKU, SKU名称) 为唯一键，取「日期」最大的一条；
      2. 拉取范围 —— 按「日期」逐天精确过滤只取最近 RECENT_DAYS(默认2) 天
         （每天 1 个请求），既快又能兜底「当日尚未写全」的 SKU（自动回退到前一天的值）；
      3. 落库口径 —— 先清空本租户 overseas_inventories，再写入本次去重结果；
         源表删除/消失的 SKU 会随之消失，不会残留脏数据；
      4. 不累计历史 —— 历史上某天的旧库存对补货决策无业务价值，
         若追加写入会以每天 499 条的规模无限膨胀。

二、展示口径
    按 (sku, product_name) 回写到 inventory_snapshots 最新快照的「主行」
    （summary_flag != '共享库存'），子行不落值，避免展开子行后重复计数。
"""

import time
import threading
from datetime import datetime, date, timedelta, timezone
from typing import Optional, Dict, List, Tuple

import requests
from sqlalchemy import text, func

from config import get_settings

_settings = get_settings()

# 复用 FBA 同步应用的凭据
APP_ID = _settings.FEISHU_SYNC_APP_ID
APP_SECRET = _settings.FEISHU_SYNC_APP_SECRET
BASE_TOKEN = _settings.FEISHU_OVERSEAS_BASE_TOKEN
TABLE_ID = _settings.FEISHU_OVERSEAS_TABLE_ID

FEISHU_API_BASE = "https://open.feishu.cn/open-apis"
# 飞书单页上限（多维表 records 列表接口最大 500）
PAGE_SIZE = 500
MAX_PAGES = 200
# 增量同步抓取的天数（1 = 只要最新一天；2 = 多取一天兜底当日未写全的 SKU）
RECENT_DAYS = 2
# 业务时区固定 UTC+8：飞书「日期」字段按北京时间落库，显式指定可避免服务器时区不同导致取错日期
CN_TZ = timezone(timedelta(hours=8))
# 从今天往前最多回溯几天找「最近有数据的日子」（源表偶有整天缺失）
LOOKBACK_DAYS = 7

# 源表字段名（飞书多维表列名，改动需同步调整）
F_SKU = "SKU"
F_NAME = "SKU名称"
F_QTY = "可售"
F_AGE = "最大库龄"
F_DATE = "日期"
F_DATE_TEXT = "日期文本"
F_SITE = "站点"

# 内存状态（仅当前进程有效，与 feishu_sync_service 保持一致的实现口径）
_sync_status = {
    "is_running": False,
    "progress": 0,
    "total": 0,
    "updated": 0,
    "matched": 0,
    "error": None,
    "finished_at": None,
    "step": "",
    # 源表最新数据日期（飞书「日期」字段）与回写的库存快照日期
    "source_date": None,
    "snapshot_date": None,
    # 本次是否被跳过（如源表数据比库中更旧），非 None 时前端提示警告而不是成功
    "skipped": None,
}

_token_cache = {"token": None, "expire_at": 0.0}
# 启动互斥锁：FastAPI 同步端点在多线程池中执行，「检查 is_running + 置位」需要原子化
_start_lock = threading.Lock()


class OverseasSyncError(Exception):
    """海外仓同步业务异常（配置缺失、飞书返回错误等）"""


def is_configured() -> bool:
    """是否已配置海外仓多维表与飞书应用凭据"""
    return bool(APP_ID and APP_SECRET and BASE_TOKEN and TABLE_ID)


def get_feishu_token(force_refresh: bool = False) -> str:
    """获取 tenant_access_token（带内存缓存，提前 5 分钟过期）"""
    now = time.time()
    if not force_refresh and _token_cache["token"] and _token_cache["expire_at"] > now:
        return _token_cache["token"]

    if not APP_ID or not APP_SECRET:
        raise OverseasSyncError("未配置飞书应用凭据 FEISHU_SYNC_APP_ID / FEISHU_SYNC_APP_SECRET")

    resp = requests.post(
        f"{FEISHU_API_BASE}/auth/v3/tenant_access_token/internal",
        json={"app_id": APP_ID, "app_secret": APP_SECRET},
        timeout=20,
    )
    result = resp.json()
    if result.get("code") != 0:
        raise OverseasSyncError(f"获取飞书 token 失败: {result.get('msg')}")

    token = result["tenant_access_token"]
    expire_in = int(result.get("expire") or 7200)
    _token_cache["token"] = token
    _token_cache["expire_at"] = now + max(expire_in - 300, 60)
    return token


def _fetch_page(token: str, page_token: Optional[str], retries: int = 5) -> dict:
    """拉取单页记录（全量顺序扫描；带重试，飞书偶发 1254607 Data not ready / 连接重置）"""
    url = f"{FEISHU_API_BASE}/bitable/v1/apps/{BASE_TOKEN}/tables/{TABLE_ID}/records"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"page_size": PAGE_SIZE}
    if page_token:
        params["page_token"] = page_token

    last_msg = ""
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=60)
            result = resp.json()
            if result.get("code") == 0:
                return result
            last_msg = f"code={result.get('code')} msg={result.get('msg')}"
            # token 失效时刷新一次
            if result.get("code") in (99991661, 99991663, 99991668):
                headers = {"Authorization": f"Bearer {get_feishu_token(force_refresh=True)}"}
        except Exception as e:  # 连接重置/超时
            last_msg = str(e)
        time.sleep(1.5 * (attempt + 1))

    raise OverseasSyncError(f"拉取海外仓多维表失败: {last_msg}")


def _search_day(token: str, day: date, retries: int = 4) -> dict:
    """按「日期」精确过滤搜索单页（一天约 499 条，单页即可取全）

    说明：多维表 search 接口在本表的 page_token 分页不可用（始终返回占位 token 且
    has_more 恒为 True），因此不使用翻页，改为「按天精确过滤 + 单页取全」；
    若某天记录数超过单页上限（has_more=True），由上层回退到全表顺序扫描。
    """
    url = f"{FEISHU_API_BASE}/bitable/v1/apps/{BASE_TOKEN}/tables/{TABLE_ID}/records/search"
    headers = {"Authorization": f"Bearer {token}"}
    # 显式按 UTC+8 计算当天 0 点，避免服务器时区不同导致过滤到错误的一天
    day_start = datetime(day.year, day.month, day.day, tzinfo=CN_TZ)
    body = {
        "page_size": PAGE_SIZE,
        "automatic_fields": False,
        "field_names": [F_SKU, F_NAME, F_QTY, F_AGE, F_DATE, F_SITE],
        "filter": {
            "conjunction": "and",
            "conditions": [
                {"field_name": F_DATE, "operator": "is",
                 "value": ["ExactDate", str(int(day_start.timestamp() * 1000))]},
            ],
        },
    }

    last_msg = ""
    for attempt in range(retries):
        try:
            result = requests.post(url, headers=headers, json=body, timeout=60).json()
            if result.get("code") == 0:
                return result
            last_msg = f"code={result.get('code')} msg={result.get('msg')}"
            if result.get("code") in (99991661, 99991663, 99991668):
                headers = {"Authorization": f"Bearer {get_feishu_token(force_refresh=True)}"}
        except Exception as e:
            last_msg = str(e)
        time.sleep(1.5 * (attempt + 1))

    raise OverseasSyncError(f"按日期搜索海外仓多维表失败({day}): {last_msg}")


def _to_float(value) -> Optional[float]:
    """飞书数字/文本字段统一转 float"""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, list):
        # 文本/公式字段可能返回 [{"text": "26", "type": "text"}]
        for item in value:
            if isinstance(item, dict) and item.get("text") not in (None, ""):
                return _to_float(item["text"])
        return None
    if isinstance(value, dict):
        return _to_float(value.get("text") or value.get("value"))
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _to_text(value) -> str:
    """飞书文本字段（可能是字符串或富文本数组）转字符串"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
            else:
                parts.append(str(item))
        return "".join(parts).strip()
    if isinstance(value, dict):
        return str(value.get("text") or "").strip()
    return str(value).strip()


def _to_date(fields: dict) -> Optional[date]:
    """取源数据日期：优先「日期」毫秒时间戳，回退「日期文本」(YYYYMMDD)"""
    raw = fields.get(F_DATE)
    ts = _to_float(raw)
    if ts:
        try:
            return datetime.fromtimestamp(ts / 1000, CN_TZ).date()
        except (ValueError, OSError, OverflowError):
            pass

    raw_text = fields.get(F_DATE_TEXT)
    if isinstance(raw_text, list) and raw_text:
        raw_text = raw_text[0].get("text") if isinstance(raw_text[0], dict) else raw_text[0]
    txt = str(raw_text or "").strip()
    if len(txt) >= 8 and txt[:8].isdigit():
        try:
            return date(int(txt[:4]), int(txt[4:6]), int(txt[6:8]))
        except ValueError:
            pass
    return None


def _parse_record(item: dict) -> Optional[dict]:
    """解析单条飞书记录，缺少 SKU 的记录直接丢弃"""
    fields = item.get("fields") or {}
    sku = _to_text(fields.get(F_SKU))
    if not sku:
        return None

    name = _to_text(fields.get(F_NAME)) or sku
    return {
        "sku": sku,
        "product_name": name,
        # 品名为空（源表直接把 SKU 写进名称列）时标记为弱约束，回写时降级为 SKU 单键匹配
        "name_missing": name == sku,
        "available_qty": _to_float(fields.get(F_QTY)) or 0.0,
        "max_age": _to_float(fields.get(F_AGE)),
        "site": _to_text(fields.get(F_SITE)) or "",
        "record_date": _to_date(fields),
        "feishu_record_id": item.get("record_id") or item.get("id") or "",
    }


def _fetch_recent_days(token: str, progress_cb=None, days: int = 1) -> List[dict]:
    """按「日期」逐天精确拉取最近 days 个有数据的日子（每天 1 个请求）

    多取一天的目的：若源表当天尚未写全（某 SKU 当日缺记录），
    去重时会自动回退到它前一天的最近一条，避免被误清成 0。
    """
    rows: List[dict] = []
    found = 0
    back = 0
    today = datetime.now(CN_TZ).date()

    while found < days and back < LOOKBACK_DAYS:
        day = today - timedelta(days=back)
        back += 1

        result = _search_day(token, day)
        data = result.get("data") or {}
        items = data.get("items") or []
        if data.get("has_more"):
            # 单日记录数超过单页上限，无法安全取全 → 交给全表扫描兜底
            raise OverseasSyncError(f"{day} 当天记录超过单页上限（{PAGE_SIZE}），改走全表扫描")
        if not items:
            continue

        found += 1
        for item in items:
            parsed = _parse_record(item)
            if not parsed:
                continue
            # 精确按天过滤，record_date 解析失败时直接用过滤日兜底
            parsed["record_date"] = parsed.get("record_date") or day
            rows.append(parsed)
        if progress_cb:
            progress_cb(len(rows), found)

    if not rows:
        raise OverseasSyncError(f"最近 {LOOKBACK_DAYS} 天内未取到任何海外仓数据")

    return rows


def _fetch_all_pages(token: str, progress_cb=None) -> List[dict]:
    """顺序扫描全表所有页（兜底；飞书表记录量大时较慢）"""
    rows: List[dict] = []
    page_token: Optional[str] = None
    page = 0

    while True:
        result = _fetch_page(token, page_token)
        data = result.get("data") or {}
        for item in data.get("items") or []:
            parsed = _parse_record(item)
            if parsed:
                rows.append(parsed)
        page += 1
        if progress_cb:
            progress_cb(len(rows), page)

        page_token = data.get("page_token")
        if not data.get("has_more") or not page_token:
            break
        if page >= MAX_PAGES:
            break

    return rows


def fetch_overseas_records(progress_cb=None, latest_only: bool = True) -> List[dict]:
    """拉取海外仓多维表记录

    latest_only=True（默认）：按「日期」逐天精确过滤，只取最近 RECENT_DAYS 天
    （每天 1 个请求，约 499 条），数秒内完成，再由 dedupe_latest 按 (SKU, 品名)
    取各自最新一条；任一环节异常（含单日超单页上限）自动回退到全表顺序扫描
    （21251 条约 2 分钟），保证同步不失败。
    """
    if not is_configured():
        raise OverseasSyncError(
            "未配置海外仓多维表：请在 .env 设置 FEISHU_OVERSEAS_BASE_TOKEN / FEISHU_OVERSEAS_TABLE_ID"
        )

    token = get_feishu_token()

    if latest_only:
        try:
            rows = _fetch_recent_days(token, progress_cb, days=RECENT_DAYS)
            if rows:
                return rows
            print("[overseas] 按天过滤未取到记录，回退全表扫描")
        except Exception as e:
            print(f"[overseas] 按天过滤拉取失败，回退全表扫描: {e}")

    return _fetch_all_pages(token, progress_cb)


def dedupe_latest(rows: List[dict]) -> Tuple[List[dict], Optional[date]]:
    """以 (SKU, 品名) 为唯一键去重，保留「日期」最大的一条

    返回 (最新全量列表, 本次同步的源数据日期)
    """
    latest: Dict[Tuple[str, str], dict] = {}
    for row in rows:
        key = (row["sku"], row["product_name"])
        current = latest.get(key)
        if current is None:
            latest[key] = row
            continue
        cur_date = current.get("record_date")
        new_date = row.get("record_date")
        # 缺日期的记录排在最后（视为最旧）
        if new_date and (cur_date is None or new_date >= cur_date):
            latest[key] = row

    result = list(latest.values())
    dates = [r["record_date"] for r in result if r.get("record_date")]
    return result, (max(dates) if dates else None)


def apply_overseas_to_snapshot(db, tenant_id: int, snapshot_date) -> int:
    """把 overseas_inventories 的最新值回写到指定快照日期的主行

    步骤：主行清零 → 强匹配(SKU+品名) → 弱匹配(品名为空时按 SKU) → COUNT 统计命中行数。
    调用方负责 commit。子行不落值，避免展开子行后重复计数。
    """
    # 只清主行（子行从不落值，无需参与写入，可减少一半写放大）
    db.execute(
        text("""
            UPDATE inventory_snapshots
            SET overseas_inventory = 0, overseas_max_age = NULL
            WHERE tenant_id = :tid AND snapshot_date = :sdate AND deleted_at IS NULL
              AND (summary_flag IS NULL OR summary_flag <> '共享库存')
        """),
        {"tid": tenant_id, "sdate": snapshot_date},
    )

    # 主行 = 非「共享库存」子行（含汇总行「是」与普通行「0」）
    base_where = """
        s.tenant_id = :tid AND s.snapshot_date = :sdate AND s.deleted_at IS NULL
        AND (s.summary_flag IS NULL OR s.summary_flag <> '共享库存')
    """
    primary = {"tid": tenant_id, "sdate": snapshot_date}

    # 强约束：SKU + 品名 双键精确匹配（一条 JOIN UPDATE 完成，避免逐条 UPDATE）
    db.execute(
        text(f"""
            UPDATE inventory_snapshots s
            JOIN overseas_inventories o
              ON o.tenant_id = s.tenant_id
             AND o.sku = s.sku
             AND o.product_name = s.product_name
            SET s.overseas_inventory = o.available_qty,
                s.overseas_max_age = o.max_age
            WHERE {base_where}
        """),
        primary,
    )

    # 弱约束：源表品名缺失（名称列直接填了 SKU）时降级为 SKU 单键匹配
    db.execute(
        text(f"""
            UPDATE inventory_snapshots s
            JOIN overseas_inventories o
              ON o.tenant_id = s.tenant_id
             AND o.sku = s.sku
             AND o.product_name = o.sku
            SET s.overseas_inventory = o.available_qty,
                s.overseas_max_age = o.max_age
            WHERE {base_where}
        """),
        primary,
    )

    # 命中行数用 COUNT 精确统计：UPDATE 的 rowcount 只统计「值发生变化的行」，
    # 会把 可售=0 的命中行与值未变的行漏掉，导致状态文案偏小。
    return db.execute(
        text(f"""
            SELECT COUNT(DISTINCT s.id)
            FROM inventory_snapshots s
            JOIN overseas_inventories o
              ON o.tenant_id = s.tenant_id
             AND o.sku = s.sku
             AND (o.product_name = s.product_name OR o.product_name = o.sku)
            WHERE {base_where}
        """),
        primary,
    ).scalar() or 0


def ensure_latest_snapshot_backfilled(db, tenant_id: int = 1) -> Optional[int]:
    """新一天的库存快照导入后，把海外仓值补写到该快照的主行（幂等）

    背景：inventory_snapshots 按天新建行，新导入的行 overseas_inventory 默认为 0。
    若每天只在 8:00 同步一次，而库存导入发生在 8:00 之后，当天页面会显示全 0。
    这里做一次廉价自查：最新快照若完全没有海外仓值，就按明细表重新回写一遍。

    返回 None 表示无需回写，否则返回本次回写的主行数。
    """
    from models.restock import InventorySnapshot

    latest = db.query(func.max(InventorySnapshot.snapshot_date)).filter(
        InventorySnapshot.tenant_id == tenant_id,
        InventorySnapshot.deleted_at.is_(None),
    ).scalar()
    if not latest:
        return None

    # 明细表为空（从未同步过）→ 无需回写
    if not db.execute(
        text("SELECT COUNT(*) FROM overseas_inventories WHERE tenant_id = :tid"),
        {"tid": tenant_id},
    ).scalar():
        return None

    # 最新快照的主行已经有海外仓值 → 说明已回写过，无需重复
    applied = db.execute(
        text("""
            SELECT COUNT(*) FROM inventory_snapshots
            WHERE tenant_id = :tid AND snapshot_date = :sdate AND deleted_at IS NULL
              AND (summary_flag IS NULL OR summary_flag <> '共享库存')
              AND COALESCE(overseas_inventory, 0) <> 0
        """),
        {"tid": tenant_id, "sdate": latest},
    ).scalar()
    if applied:
        return None

    matched = apply_overseas_to_snapshot(db, tenant_id, latest)
    db.commit()
    return matched


def write_to_db(db, tenant_id: int, rows: List[dict], record_date: Optional[date] = None) -> dict:
    """覆盖式写入 overseas_inventories，并回写最新快照主行

    返回 {"inserted": n, "snapshot_matched": n, "snapshot_date": date|None, "skipped": str|None}
    """
    from models.restock import InventorySnapshot

    # 0. 陈旧源数据保护：源表数据比库里的还旧（上游回退/补数/清空当天行）时，
    #    拒绝用旧数据覆盖已在库的新数据，直接跳过本次写入。
    if rows and record_date:
        stored_max = db.execute(
            text("SELECT MAX(record_date) FROM overseas_inventories WHERE tenant_id = :tid"),
            {"tid": tenant_id},
        ).scalar()
        if stored_max and record_date < stored_max:
            db.rollback()
            return {
                "inserted": 0,
                "snapshot_matched": 0,
                "snapshot_date": None,
                "skipped": "stale_source",
                "reason": f"源表数据日期 {record_date} 早于库中最新 {stored_max}，已跳过写入",
            }

    # 1. 覆盖本租户海外仓数据（源表是全量快照，覆盖优于追加）
    db.execute(text("DELETE FROM overseas_inventories WHERE tenant_id = :tid"), {"tid": tenant_id})
    db.flush()

    if rows:
        # 逐行 executemany 在远程库上会退化为「每行一次往返」（499 行约 19s），
        # 这里拼成多行 VALUES 单语句，2 次往返即可完成
        for start in range(0, len(rows), 250):
            chunk = rows[start:start + 250]
            placeholders, params = [], {}
            for idx, r in enumerate(chunk):
                placeholders.append(
                    f"(:tid{idx}, :sku{idx}, :name{idx}, :site{idx}, :qty{idx}, "
                    f":age{idx}, :rdate{idx}, :rid{idx}, NOW(), NOW())"
                )
                params[f"tid{idx}"] = tenant_id
                params[f"sku{idx}"] = (r["sku"] or "")[:255]
                params[f"name{idx}"] = (r["product_name"] or "")[:255]
                params[f"site{idx}"] = (r["site"] or "")[:100]
                params[f"qty{idx}"] = r["available_qty"]
                params[f"age{idx}"] = r["max_age"]
                params[f"rdate{idx}"] = r["record_date"]
                params[f"rid{idx}"] = (r["feishu_record_id"] or "")[:100]
            db.execute(
                text(
                    "INSERT INTO overseas_inventories "
                    "(tenant_id, sku, product_name, site, available_qty, max_age, "
                    "record_date, feishu_record_id, created_at, updated_at) VALUES "
                    + ", ".join(placeholders)
                ),
                params,
            )

    # 2. 回写库存快照「主行」
    latest = db.query(func.max(InventorySnapshot.snapshot_date)).filter(
        InventorySnapshot.tenant_id == tenant_id,
        InventorySnapshot.deleted_at.is_(None),
    ).scalar()

    matched = 0
    if latest:
        matched = apply_overseas_to_snapshot(db, tenant_id, latest)

    db.commit()
    return {
        "inserted": len(rows),
        "snapshot_matched": matched,
        "snapshot_date": latest.isoformat() if latest else None,
        "skipped": None,
        "reason": None,
    }


def get_sync_status() -> dict:
    """获取当前同步状态"""
    return _sync_status.copy()


def _update_status(**kwargs):
    _sync_status.update(kwargs)


def _do_sync(tenant_id: int):
    """后台同步任务"""
    from database.database import SessionLocal

    db = SessionLocal()
    try:
        _update_status(
            is_running=True, progress=2, total=0, updated=0, matched=0,
            error=None, finished_at=None, step="连接飞书多维表...",
            source_date=None, snapshot_date=None, skipped=None,
        )

        def on_page(fetched: int, day_no: int):
            _update_status(
                progress=min(2 + day_no * 15, 60),
                total=fetched,
                step=f"拉取飞书最近 {RECENT_DAYS} 天记录 {fetched} 条（第 {day_no} 天）",
            )

        raw_rows = fetch_overseas_records(progress_cb=on_page)
        if not raw_rows:
            _update_status(is_running=False, progress=100, step="飞书表无数据，未做任何写入")
            return

        _update_status(progress=62, total=len(raw_rows), step="按 SKU+品名 去重取最新日期...")
        latest_rows, record_date = dedupe_latest(raw_rows)

        _update_status(
            progress=70,
            step=f"去重完成：{len(raw_rows)} 条 → {len(latest_rows)} 个 SKU，写入数据库...",
        )

        result = write_to_db(db, tenant_id, latest_rows, record_date)

        if result.get("skipped"):
            _update_status(
                is_running=False,
                progress=100,
                skipped=result["skipped"],
                source_date=record_date.isoformat() if record_date else None,
                finished_at=datetime.now().isoformat(),
                step=f"已跳过：{result.get('reason')}",
            )
            return

        _update_status(
            is_running=False,
            progress=100,
            skipped=None,
            updated=result["inserted"],
            matched=result["snapshot_matched"],
            source_date=record_date.isoformat() if record_date else None,
            snapshot_date=result["snapshot_date"],
            finished_at=datetime.now().isoformat(),
            step=(
                f"同步完成！源数据日期 {record_date or '-'}，去重后 {result['inserted']} 个 SKU，"
                f"主行写入 {result['snapshot_matched']} 行"
            ),
        )

    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        _update_status(
            is_running=False,
            error=str(e),
            step=f"同步失败: {e}",
            finished_at=datetime.now().isoformat(),
        )
        import traceback
        traceback.print_exc()
    finally:
        db.close()


def start_sync_async(tenant_id: int = 1, force: bool = False) -> dict:
    """启动异步同步任务（同一时间仅允许一个任务）

    返回 {"started": bool, "reason": "running"|"not_configured"|"thread_failed"|None, "message": str}
    reason 供前端判断：running 表示可直接接入轮询，其余表示无需等待。
    """
    if not is_configured():
        return {
            "started": False,
            "reason": "not_configured",
            "message": "未配置海外仓多维表，请在 .env 设置 FEISHU_OVERSEAS_BASE_TOKEN / FEISHU_OVERSEAS_TABLE_ID",
        }

    # 检查与置位原子化，避免两个请求（手动点击 + 定时任务）同时通过检查各起一个线程
    with _start_lock:
        if _sync_status["is_running"] and not force:
            return {"started": False, "reason": "running", "message": "海外仓同步任务正在运行中"}

        _update_status(
            is_running=True, progress=0, total=0, updated=0, matched=0,
            error=None, finished_at=None, step="任务已启动",
            source_date=None, snapshot_date=None, skipped=None,
        )

    try:
        thread = threading.Thread(target=_do_sync, args=(tenant_id,), daemon=True)
        thread.start()
    except Exception as e:
        # 线程起不来时回收状态，避免前端永远显示「同步中」
        _update_status(is_running=False, error=f"启动后台线程失败: {e}", step=f"启动失败: {e}")
        return {"started": False, "reason": "thread_failed", "message": f"启动后台线程失败: {e}"}

    return {"started": True, "reason": None, "message": "海外仓同步任务已启动"}


def get_overseas_summary(db, tenant_id: int = 1) -> dict:
    """海外仓数据汇总（前端 KPI / 提示用）"""
    row = db.execute(
        text("""
            SELECT COUNT(*) AS total_sku,
                   COALESCE(SUM(available_qty), 0) AS total_qty,
                   MAX(record_date) AS latest_record_date,
                   MAX(updated_at) AS last_synced_at
            FROM overseas_inventories
            WHERE tenant_id = :tid
        """),
        {"tid": tenant_id},
    ).fetchone()
    if not row:
        return {"total_sku": 0, "total_qty": 0, "latest_record_date": None, "last_synced_at": None}
    return {
        "total_sku": int(row[0] or 0),
        "total_qty": float(row[1] or 0),
        "latest_record_date": row[2].isoformat() if row[2] else None,
        "last_synced_at": row[3].isoformat() if row[3] else None,
    }
