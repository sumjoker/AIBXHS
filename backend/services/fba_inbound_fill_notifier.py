#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FBA 在途信息补填通知

同步链路收尾（services/feishu_sync_service.py::_do_sync 正常结束后）调用本模块：
按 (tenant_id, shipment_id) 校验「发货日期 / 运输方式」是否缺失，
缺失则给「仓库角色」（roles.code='warehouseman'）下的在职用户发站内通知，
提醒前往飞书多维表补填（飞书列：发货日期 / 渠道（仓库填））。

设计约定（coding 文档 2026-11-05 §2）：
- 只校验 ship_date / transport_method 两个字段，不扩展
- 生效快照口径：每租户 MAX(inventory_snapshots.snapshot_date)（deleted_at IS NULL），
  与 get_inbound_details() 的快照口径一致
- 收件人：roles.code='warehouseman' 且 users.status='active'（不用「仓库部」部门，
  实测该部门 0 成员）；租户无该角色 → 跳过并记 INFO 日志
- 去重：tenant_id + user_id + title + content 完全一致且 24 小时内 → 跳过
- 失败处理：整体 try/except，只记日志、不抛出、不阻塞同步
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List

from sqlalchemy import text
from sqlalchemy.orm import Session

from database.database import SessionLocal

logger = logging.getLogger(__name__)

# 常量（coding 文档 §2.3/§2.4/§2.5）
WAREHOUSE_ROLE_CODE = "warehouseman"          # 仓库角色 roles.code
NOTIFY_TITLE = "FBA在途信息补填提醒"            # 固定标题，便于去重与检索
NOTIFY_TYPE = "warning"                       # MainLayout 显示为「警告」
NOTIFY_LINK = "/inventory"                    # 库存机器人页
DEDUP_HOURS = 24                              # 同内容去重窗口
MAX_LISTED_FBA = 50                           # content 最长列出 50 个 FBA 编号

MISS_SHIP_DATE_LABEL = "发货日期"
MISS_TRANSPORT_LABEL = "运输方式"


def _fmt_date(value) -> str:
    """批次日期统一输出 YYYY-MM-DD；异常/空值兜底为空串"""
    if value is None:
        return ""
    if hasattr(value, "strftime"):
        try:
            return value.strftime("%Y-%m-%d")
        except Exception:
            pass
    return str(value)[:10]


def collect_missing_fba(db: Session) -> Dict[int, dict]:
    """收集各租户「发货日期 / 运输方式」缺失的 FBA 编号

    返回结构（coding 文档 §4.1 允许实现自定）：
        {tenant_id: {"max_date": <该租户最新快照日期>, "items": [
            {"shipment_id": "FBA19VWL41D3",
             "miss_ship_date": True, "miss_transport": False}, ...]}}
    只有存在缺失的租户才会出现在结果里；同一 FBA 编号只出现一次。

    SQL 为两段式（先取每租户最新快照日，再逐租户查），兼容 MySQL 5.7 / SQLite：
    - 空值判定：ship_date IS NULL；transport_method IS NULL OR TRIM(...)=''
    - 同一 FBA 编号下任一行任一字段为空 → 该编号纳入，缺失字段取 MAX 聚合
    - 排除：明细软删、shipment_id 为空、快照软删、非该租户最新快照的行
    """
    result: Dict[int, dict] = {}

    step1 = db.execute(text(
        "SELECT tenant_id, MAX(snapshot_date) AS max_date "
        "FROM inventory_snapshots "
        "WHERE deleted_at IS NULL "
        "GROUP BY tenant_id"
    )).fetchall()

    for row in step1:
        tenant_id, max_date = row[0], row[1]
        max_date_str = _fmt_date(max_date)
        if not max_date_str:
            continue  # 该租户没有任何有效快照

        missing = db.execute(text(
            "SELECT d.shipment_id, "
            "       MAX(d.ship_date IS NULL)                                  AS miss_ship_date, "
            "       MAX(d.transport_method IS NULL OR TRIM(d.transport_method) = '') "
            "                                                                  AS miss_transport "
            "FROM inbound_shipment_details d "
            "JOIN inventory_snapshots s ON s.id = d.snapshot_id "
            "WHERE d.tenant_id = :tid "
            "  AND s.tenant_id = :tid "
            "  AND s.snapshot_date = :max_date "
            "  AND s.deleted_at IS NULL "
            "  AND d.deleted_at IS NULL "
            "  AND d.shipment_id IS NOT NULL AND TRIM(d.shipment_id) <> '' "
            "  AND (d.ship_date IS NULL "
            "       OR d.transport_method IS NULL OR TRIM(d.transport_method) = '') "
            "GROUP BY d.shipment_id "
            "ORDER BY d.shipment_id"
        ), {"tid": tenant_id, "max_date": max_date_str}).fetchall()

        items = [
            {
                "shipment_id": r[0],
                "miss_ship_date": bool(r[1]),
                "miss_transport": bool(r[2]),
            }
            for r in missing
        ]
        if items:
            result[tenant_id] = {"max_date": max_date, "items": items}

    return result


def render_content(items: List[dict], max_date) -> str:
    """按 coding 文档 §2.4 模板渲染通知内容

    - 缺失字段固定顺序「发货日期 → 运输方式」，用「、」连接
    - 最多列 MAX_LISTED_FBA 个，超出折叠为「  ...等共 N 个 FBA 编号」
    - 结尾为批次日期行，无多余空行
    """
    lines = [
        "【FBA在途信息补填提醒】",
        "本次库存数据导入后，以下 FBA 编号的发货日期或运输方式仍未填写，请仓库角色前往飞书补填：",
    ]

    for item in items[:MAX_LISTED_FBA]:
        missing = []
        if item.get("miss_ship_date"):
            missing.append(MISS_SHIP_DATE_LABEL)
        if item.get("miss_transport"):
            missing.append(MISS_TRANSPORT_LABEL)
        label = "、".join(missing) if missing else "—"
        lines.append(f"  {item.get('shipment_id')}：缺 {label}")

    if len(items) > MAX_LISTED_FBA:
        lines.append(f"  ...等共 {len(items)} 个 FBA 编号")

    lines.append("补填字段：发货日期（飞书列「发货日期」）、运输方式（飞书列「渠道（仓库填）」）。")
    lines.append("补填完成后，可在库存机器人页点击「同步FBA在途」重新同步。")
    lines.append(f"批次日期：{_fmt_date(max_date)}")
    return "\n".join(lines)


def _warehouse_user_ids(db: Session, tenant_id: int) -> List[int]:
    """「仓库角色」在职用户 id 列表（按 tenant_id 隔离）"""
    rows = db.execute(text(
        "SELECT DISTINCT u.id "
        "FROM users u "
        "JOIN roles r ON r.id = u.role_id AND r.deleted_at IS NULL "
        "WHERE u.tenant_id = :tid "
        "  AND r.code = :code "
        "  AND u.deleted_at IS NULL "
        "  AND u.status = 'active' "
        "ORDER BY u.id"
    ), {"tid": tenant_id, "code": WAREHOUSE_ROLE_CODE}).fetchall()
    return [r[0] for r in rows]


def _has_recent_duplicate(db: Session, tenant_id: int, user_id: int,
                          content: str, cutoff: str) -> bool:
    """24 小时内同 (tenant, user, title, content) 已发过 → 跳过"""
    count = db.execute(text(
        "SELECT COUNT(*) FROM notifications "
        "WHERE tenant_id = :tid AND user_id = :uid "
        "  AND title = :title AND content = :content "
        "  AND deleted_at IS NULL "
        "  AND created_at >= :cutoff"
    ), {"tid": tenant_id, "uid": user_id, "title": NOTIFY_TITLE,
        "content": content, "cutoff": cutoff}).scalar()
    return bool(count)


def notify_missing_fba_info() -> dict:
    """主入口：校验缺失 → 解析仓库角色用户 → 去重 → 写 notifications

    自建 SessionLocal（不复用 _do_sync 的 db），任何异常内部吞掉、不抛出。
    正常返回 {"ok": True, "tenants": n, "fba_count": n, "notified": n, "skipped": n}
      - tenants  : 实际发出通知的租户数（有缺失且存在仓库角色在职用户）
      - fba_count: 缺失 FBA 编号总数
      - notified : 实际写入的通知条数
      - skipped  : 因 24h 同内容去重跳过的条数
    失败返回 {"ok": False, "error": str}
    """
    db = None
    try:
        db = SessionLocal()
        collected = collect_missing_fba(db)
        if not collected:
            logger.info("FBA在途补填通知: 各租户均无缺失的发货日期/运输方式，未产生通知")
            return {"ok": True, "tenants": 0, "fba_count": 0, "notified": 0, "skipped": 0}

        now = datetime.now()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        cutoff = (now - timedelta(hours=DEDUP_HOURS)).strftime("%Y-%m-%d %H:%M:%S")

        tenants_done = 0
        fba_count = 0
        notified = 0
        skipped = 0

        for tenant_id, payload in collected.items():
            max_date = payload.get("max_date")
            items: List[dict] = payload.get("items") or []
            if not items:
                continue
            fba_count += len(items)
            content = render_content(items, max_date)

            user_ids = _warehouse_user_ids(db, tenant_id)
            if not user_ids:
                logger.info(
                    "FBA在途补填通知: 租户 %s 无「仓库」角色在职用户，跳过（缺失 %s 个 FBA 编号）",
                    tenant_id, len(items),
                )
                continue
            tenants_done += 1

            for user_id in user_ids:
                if _has_recent_duplicate(db, tenant_id, user_id, content, cutoff):
                    skipped += 1
                    continue
                db.execute(text(
                    "INSERT INTO notifications "
                    "(tenant_id, user_id, type, title, content, link, created_at, updated_at) "
                    "VALUES (:tid, :uid, :type, :title, :content, :link, :now, :now)"
                ), {"tid": tenant_id, "uid": user_id, "type": NOTIFY_TYPE,
                    "title": NOTIFY_TITLE, "content": content,
                    "link": NOTIFY_LINK, "now": now_str})
                notified += 1

        db.commit()
        logger.info(
            "FBA在途补填通知: 租户 %s 个，FBA 编号 %s 个，写入 %s 条，去重跳过 %s 条",
            tenants_done, fba_count, notified, skipped,
        )
        return {"ok": True, "tenants": tenants_done, "fba_count": fba_count,
                "notified": notified, "skipped": skipped}

    except Exception as e:  # noqa: BLE001 任何失败都不允许阻塞同步
        try:
            if db is not None:
                db.rollback()
        except Exception:
            pass
        logger.warning("FBA在途补填通知失败（不阻塞同步）: %s", e)
        return {"ok": False, "error": str(e)}
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass
