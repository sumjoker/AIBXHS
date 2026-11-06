# -*- coding: utf-8 -*-
"""FBA 在途信息补填通知 单元测试（内存 SQLite，不连正式库、不发飞书请求）

覆盖 coding 文档（2026-11-05）§5 验收标准 AC2–AC10：
- 缺失收集口径（AC2/AC6）
- 文案模板与折叠（§2.4）
- 通知对象（仓库角色）与写入（AC3）
- 24h 同内容去重（AC4）、内容变化去重失效（AC5）
- 同步失败不通知（AC7）、收件人异常不阻塞同步（AC8）
- 手动入口同一链路（AC9）、无缺失不打扰（AC10）
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from sqlalchemy import create_engine, event, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import database.database as database_module  # noqa: E402
import services.feishu_sync_service as sync_service  # noqa: E402
import services.fba_inbound_fill_notifier as notifier  # noqa: E402

# ---------------------------------------------------------------------------
# 测试库结构（与真实表同名同关键列，日期列用 TEXT 存 ISO 字符串）
# ---------------------------------------------------------------------------
DDL = [
    "CREATE TABLE inventory_snapshots ("
    " id INTEGER PRIMARY KEY, tenant_id INTEGER, snapshot_date TEXT, deleted_at TEXT)",
    "CREATE TABLE inbound_shipment_details ("
    " id INTEGER PRIMARY KEY, tenant_id INTEGER, snapshot_id INTEGER, shipment_id TEXT,"
    " ship_date TEXT, transport_method TEXT, estimated_arrival_date TEXT,"
    " updated_at TEXT, deleted_at TEXT)",
    "CREATE TABLE users ("
    " id INTEGER PRIMARY KEY, tenant_id INTEGER, role_id INTEGER, status TEXT, deleted_at TEXT)",
    "CREATE TABLE roles ("
    " id INTEGER PRIMARY KEY, tenant_id INTEGER, code TEXT, deleted_at TEXT)",
    "CREATE TABLE notifications ("
    " id INTEGER PRIMARY KEY, tenant_id INTEGER, user_id INTEGER, type TEXT, title TEXT,"
    " content TEXT, link TEXT, read_at TEXT, created_at TEXT, updated_at TEXT, deleted_at TEXT)",
]

WAREHOUSE_USERS = (17, 32, 45, 46)          # 租户 1 仓库角色在职用户
FBA_MISSING_MAIN = "FBA19VWL41D3"           # 缺发货日期（2 行）
FBA_MISSING_SECOND = "FBA19V0DHHTX"         # 缺发货日期（1 行）
SNAP_DATE = "2026-11-04"


@pytest.fixture()
def db_session(monkeypatch):
    """内存 SQLite：建表 + 注册 NOW() + 把 notifier/_do_sync 的 Session 指向它"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _register_now(dbapi_conn, _):  # noqa: ANN001
        dbapi_conn.create_function(
            "NOW", 0, lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    with engine.begin() as conn:
        for ddl in DDL:
            conn.execute(text(ddl))

    test_session = sessionmaker(bind=engine)
    session = test_session()

    # notifier 自建 Session / _do_sync 的 SessionLocal 都指向测试库
    monkeypatch.setattr(notifier, "SessionLocal", test_session)
    monkeypatch.setattr(database_module, "SessionLocal", test_session)

    _seed(session)
    yield session, engine

    session.close()


# ---------------------------------------------------------------------------
# 造数
# ---------------------------------------------------------------------------
def _seed(session):
    session.execute(text(
        "INSERT INTO inventory_snapshots (id, tenant_id, snapshot_date, deleted_at)"
        " VALUES (1, 1, :d, NULL), (2, 1, '2026-11-01', NULL), (3, 6, :d, NULL)"),
        {"d": SNAP_DATE})

    rows, seq = [], [0]

    def detail(snapshot_id, shipment_id, ship_date, transport, deleted_at="NULL", tenant=1):
        seq[0] += 1
        rows.append(
            f"({seq[0]}, {tenant}, {snapshot_id}, '{shipment_id}', "
            f"{ship_date}, {transport}, {deleted_at})")

    # 租户 1 / 最新快照：两个缺失发货日期的 FBA 编号
    detail(1, FBA_MISSING_MAIN, "NULL", "'海运'")
    detail(1, FBA_MISSING_MAIN, "NULL", "'空运'")
    detail(1, FBA_MISSING_SECOND, "NULL", "'海运'")
    # 已填写（不进入结果）
    detail(1, "FBA19VOK", "'2026-10-01'", "'空运'")
    # 软删（排除）
    detail(1, "FBA19VDEL", "NULL", "'海运'", deleted_at="'2026-11-02 10:00:00'")
    # shipment_id 为空（排除）
    detail(1, "", "NULL", "'海运'")
    # 旧快照（排除）
    detail(2, "FBA19VOLD", "NULL", "'海运'")
    # 租户 6（有缺失但无仓库角色 → 跳过通知）
    detail(3, "FBA19VT6", "NULL", "'海运'", tenant=6)

    session.execute(text(
        "INSERT INTO inbound_shipment_details "
        "(id, tenant_id, snapshot_id, shipment_id, ship_date, transport_method, deleted_at)"
        " VALUES " + ",".join(rows)))

    session.execute(text(
        "INSERT INTO roles (id, tenant_id, code, deleted_at)"
        " VALUES (1, 1, 'warehouseman', NULL), (2, 6, 'admin', NULL)"))
    users = [f"({u}, 1, 1, 'active', NULL)" for u in WAREHOUSE_USERS]
    users.append("(47, 1, 1, 'disabled', NULL)")       # 非在职 → 不通知
    users.append("(60, 6, 2, 'active', NULL)")         # 租户 6 无仓库角色 → 不通知
    session.execute(text(
        "INSERT INTO users (id, tenant_id, role_id, status, deleted_at)"
        " VALUES " + ",".join(users)))
    session.commit()


def _notification_rows(session):
    return [dict(r._mapping) for r in session.execute(text(
        "SELECT tenant_id, user_id, type, title, content, link FROM notifications "
        "ORDER BY user_id")).fetchall()]


# ---------------------------------------------------------------------------
# AC2 / AC6：缺失收集口径
# ---------------------------------------------------------------------------
def test_collect_missing_fba_scope(db_session):
    session, _ = db_session
    result = notifier.collect_missing_fba(session)

    # 只有存在缺失的租户才出现
    assert set(result.keys()) == {1, 6}
    t1 = result[1]
    assert t1["max_date"] == SNAP_DATE or str(t1["max_date"])[:10] == SNAP_DATE

    ids = [i["shipment_id"] for i in t1["items"]]
    assert ids == [FBA_MISSING_SECOND, FBA_MISSING_MAIN]   # 字典序
    assert all(i["miss_ship_date"] is True for i in t1["items"])
    assert all(i["miss_transport"] is False for i in t1["items"])

    # 租户 6 有缺失（会被收集），通知阶段因无仓库角色而跳过
    assert [i["shipment_id"] for i in result[6]["items"]] == ["FBA19VT6"]


def test_collect_empty_value_variants(db_session):
    """AC6：transport NULL/''/'   ' 都算缺失；非空不算；排除口径生效"""
    session, _ = db_session
    session.execute(text(
        "INSERT INTO inbound_shipment_details "
        "(id, tenant_id, snapshot_id, shipment_id, ship_date, transport_method, deleted_at)"
        " VALUES "
        " (900, 1, 1, 'FBA19VNULL',  NULL,      NULL,      NULL),"   # 运输 NULL
        " (901, 1, 1, 'FBA19VEMPTY', NULL,      '',        NULL),"   # 运输 ''
        " (902, 1, 1, 'FBA19VSPACE', NULL,      '   ',     NULL),"   # 运输全空格
        " (903, 1, 1, 'FBA19VOKTR',  NULL,      '海运',     NULL)"))  # 运输非空（不缺运输）
    session.commit()

    result = notifier.collect_missing_fba(session)
    ids = {i["shipment_id"]: i for i in result[1]["items"]}
    assert ids["FBA19VNULL"]["miss_transport"] is True
    assert ids["FBA19VEMPTY"]["miss_transport"] is True
    assert ids["FBA19VSPACE"]["miss_transport"] is True
    assert ids["FBA19VOKTR"]["miss_transport"] is False
    assert ids["FBA19VOKTR"]["miss_ship_date"] is True   # ship_date 仍为 NULL


# ---------------------------------------------------------------------------
# §2.4 文案模板
# ---------------------------------------------------------------------------
def test_render_content_template_exact(db_session):
    session, _ = db_session
    result = notifier.collect_missing_fba(session)
    content = notifier.render_content(result[1]["items"], result[1]["max_date"])

    expected = (
        "【FBA在途信息补填提醒】\n"
        "本次库存数据导入后，以下 FBA 编号的发货日期或运输方式仍未填写，请仓库角色前往飞书补填：\n"
        f"  {FBA_MISSING_SECOND}：缺 发货日期\n"
        f"  {FBA_MISSING_MAIN}：缺 发货日期\n"
        "补填字段：发货日期（飞书列「发货日期」）、运输方式（飞书列「渠道（仓库填）」）。\n"
        "补填完成后，可在库存机器人页点击「同步FBA在途」重新同步。\n"
        f"批次日期：{SNAP_DATE}"
    )
    assert content == expected


def test_render_content_both_missing_and_order():
    items = [
        {"shipment_id": "FBA-B", "miss_ship_date": True, "miss_transport": True},
        {"shipment_id": "FBA-A", "miss_ship_date": False, "miss_transport": True},
    ]
    content = notifier.render_content(items, "2026-11-04")
    assert "  FBA-B：缺 发货日期、运输方式" in content
    assert "  FBA-A：缺 运输方式" in content
    # 缺失字段固定顺序：发货日期 → 运输方式
    assert content.index("发货日期、运输方式") > 0


def test_render_content_overflow_folded():
    items = [{"shipment_id": f"FBA-{i:03d}", "miss_ship_date": True,
              "miss_transport": False} for i in range(60)]
    content = notifier.render_content(items, "2026-11-04")
    listed = [line for line in content.splitlines() if line.startswith("  FBA-")]
    assert len(listed) == notifier.MAX_LISTED_FBA            # 只列 50 个
    assert "  ...等共 60 个 FBA 编号" in content.splitlines()


# ---------------------------------------------------------------------------
# AC3 / AC10：通知写入与无缺失不打扰
# ---------------------------------------------------------------------------
def test_notify_writes_to_warehouse_users(db_session):
    session, _ = db_session
    result = notifier.notify_missing_fba_info()

    assert result["ok"] is True
    assert result["tenants"] == 1
    assert result["fba_count"] == 3          # 租户1 两个 + 租户6 一个
    assert result["notified"] == len(WAREHOUSE_USERS)
    assert result["skipped"] == 0

    rows = _notification_rows(session)
    assert [r["user_id"] for r in rows] == list(WAREHOUSE_USERS)
    assert all(r["tenant_id"] == 1 for r in rows)
    assert all(r["type"] == "warning" for r in rows)
    assert all(r["title"] == notifier.NOTIFY_TITLE for r in rows)
    assert all(r["link"] == "/inventory" for r in rows)

    content = rows[0]["content"]
    assert FBA_MISSING_MAIN in content and FBA_MISSING_SECOND in content
    assert "发货日期" in content
    assert "飞书" in content
    assert "仓库角色" in content
    assert f"批次日期：{SNAP_DATE}" in content
    assert "渠道（仓库填）" in content


def test_no_missing_no_notification(db_session):
    """AC10：全部填齐 → 0 条通知"""
    session, _ = db_session
    session.execute(text(
        "UPDATE inbound_shipment_details SET ship_date='2026-10-01', transport_method='海运' "
        "WHERE deleted_at IS NULL"))
    session.commit()

    result = notifier.notify_missing_fba_info()
    assert result == {"ok": True, "tenants": 0, "fba_count": 0, "notified": 0, "skipped": 0}
    assert _notification_rows(session) == []


# ---------------------------------------------------------------------------
# AC4 / AC5：去重
# ---------------------------------------------------------------------------
def test_dedup_same_content_24h(db_session):
    session, _ = db_session
    first = notifier.notify_missing_fba_info()
    assert first["notified"] == len(WAREHOUSE_USERS)

    second = notifier.notify_missing_fba_info()
    assert second["notified"] == 0
    assert second["skipped"] == len(WAREHOUSE_USERS)
    assert len(_notification_rows(session)) == len(WAREHOUSE_USERS)   # 无重复


def test_dedup_window_expired_writes_again(db_session):
    session, _ = db_session
    result = notifier.collect_missing_fba(session)
    content = notifier.render_content(result[1]["items"], result[1]["max_date"])
    old = (datetime.now() - timedelta(hours=notifier.DEDUP_HOURS + 2)
           ).strftime("%Y-%m-%d %H:%M:%S")
    session.execute(text(
        "INSERT INTO notifications (tenant_id, user_id, type, title, content, link,"
        " created_at, updated_at, deleted_at) VALUES (1, 17, 'warning', :title,"
        " :content, '/inventory', :created, :created, NULL)"),
        {"title": notifier.NOTIFY_TITLE, "content": content, "created": old})
    session.commit()

    result = notifier.notify_missing_fba_info()
    # 25 小时前的同内容通知不在去重窗口内 → 4 人全量写入
    assert result["notified"] == len(WAREHOUSE_USERS)


def test_content_change_reallows_notification(db_session):
    """AC5：补填部分字段后 content 变化 → 允许再发一条，且只剩 1 个 FBA 编号"""
    session, _ = db_session
    notifier.notify_missing_fba_info()

    # 补填其中一个 FBA 编号的发货日期
    session.execute(text(
        "UPDATE inbound_shipment_details SET ship_date='2026-11-03' "
        "WHERE shipment_id = :sid"), {"sid": FBA_MISSING_SECOND})
    session.commit()

    second = notifier.notify_missing_fba_info()
    assert second["ok"] is True
    assert second["notified"] == len(WAREHOUSE_USERS)
    assert second["fba_count"] == 2                # 租户1 剩 1 个 + 租户6 1 个

    rows = _notification_rows(session)
    tenant1 = [r for r in rows if r["tenant_id"] == 1]
    assert len(tenant1) == len(WAREHOUSE_USERS) * 2   # 两个不同 content → 各一条
    latest = tenant1[-1]["content"]
    assert FBA_MISSING_MAIN in latest
    assert FBA_MISSING_SECOND not in latest


# ---------------------------------------------------------------------------
# AC7 / AC8 / AC9：与 _do_sync 收尾的衔接
# ---------------------------------------------------------------------------
def _patch_fetch(monkeypatch, records):
    monkeypatch.setattr(sync_service, "fetch_feishu_inbound_data_fast",
                        lambda days=90: records)


def test_sync_failure_sends_no_notification(db_session, monkeypatch):
    """AC7：同步抛异常 → 0 条通知，状态机正常"""
    session, _ = db_session

    def _boom(days=90):
        raise Exception("获取飞书token失败")

    _patch_fetch(monkeypatch, None)
    monkeypatch.setattr(sync_service, "fetch_feishu_inbound_data_fast", _boom)

    sync_service._do_sync()   # 不应抛出

    assert sync_service._sync_status["is_running"] is False
    assert "token" in sync_service._sync_status["error"]
    assert _notification_rows(session) == []


def test_sync_success_notifies_before_status_closed(db_session, monkeypatch):
    """AC9 + 硬约束：通知在 is_running=False 之前执行；手动/自动入口同一链路"""
    session, engine = db_session
    test_session = sessionmaker(bind=session.get_bind())
    # 飞书侧该 FBA 发货日期仍为空 → 同步后仍缺失 → 需要通知
    _patch_fetch(monkeypatch, [{
        "shipment_id": FBA_MISSING_MAIN,
        "transport_method": "海运",
        "estimated_arrival_date": None,
        "ship_date": None,
    }])

    recorded = {}
    real_notify = notifier.notify_missing_fba_info

    def _spy():
        recorded["during"] = sync_service._sync_status["is_running"]
        return real_notify()

    monkeypatch.setattr(notifier, "notify_missing_fba_info", _spy)

    sync_service._do_sync()

    assert sync_service._sync_status["is_running"] is False
    assert sync_service._sync_status["error"] is None
    assert recorded["during"] is True                  # 通知时状态仍是运行中
    assert len(_notification_rows(session)) == len(WAREHOUSE_USERS)


def test_sync_no_data_still_notifies(db_session, monkeypatch):
    """「飞书返回 0 条」也算正常结束 → 仍要通知补填"""
    session, _ = db_session
    _patch_fetch(monkeypatch, [])

    sync_service._do_sync()

    assert sync_service._sync_status["is_running"] is False
    assert sync_service._sync_status["step"] == "没有需要同步的数据"
    assert len(_notification_rows(session)) == len(WAREHOUSE_USERS)


def test_notify_failure_does_not_break_sync(db_session, monkeypatch):
    """AC8：通知模块抛异常 → 不冒泡，is_running 正常置位"""
    session, _ = db_session
    _patch_fetch(monkeypatch, [{
        "shipment_id": FBA_MISSING_MAIN,
        "transport_method": "海运",
        "estimated_arrival_date": None,
        "ship_date": None,
    }])

    def _explode():
        raise RuntimeError("通知库连不上")

    monkeypatch.setattr(notifier, "notify_missing_fba_info", _explode)

    sync_service._do_sync()   # 不应抛出

    assert sync_service._sync_status["is_running"] is False
    assert sync_service._sync_status["error"] is None
    assert _notification_rows(session) == []


def test_notify_never_raises_when_session_broken(db_session, monkeypatch):
    """AC8：SessionLocal 自身创建失败 → 返回 {"ok": False}，不抛出"""
    def _broken():
        raise RuntimeError("db down")

    monkeypatch.setattr(notifier, "SessionLocal", _broken)
    result = notifier.notify_missing_fba_info()
    assert result["ok"] is False
    assert "db down" in result["error"]


def test_tenant_without_warehouse_role_skipped(db_session):
    """租户 6 有缺失但无仓库角色 → 跳过并记日志，不影响租户 1"""
    session, _ = db_session
    result = notifier.notify_missing_fba_info()
    assert result["ok"] is True
    rows = _notification_rows(session)
    assert all(r["tenant_id"] == 1 for r in rows)
    assert all(r["user_id"] != 60 for r in rows)
