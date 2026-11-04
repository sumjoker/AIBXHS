"""
库存补货API路由 - 供影刀RPA及前端调用
"""
import os
import logging
from fastapi import APIRouter, HTTPException, Depends, Query, UploadFile, File, Request
from typing import Optional, List
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text
from database.database import get_db
from dependencies import get_current_user
from models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/restock", tags=["restock"])


def is_admin_user(user: User, db: Session) -> bool:
    """判断用户是否是管理员（通过 role_id）"""
    if not user.role_id:
        return False
    role = db.execute(text("""
        SELECT code FROM roles WHERE id = :role_id AND deleted_at IS NULL
    """), {"role_id": user.role_id}).fetchone()
    return role and role[0] == "admin"


def get_user_role_code(user: User, db: Session) -> str:
    """获取用户角色编码（通过 role_id）"""
    if not user.role_id:
        return ""
    role = db.execute(text("""
        SELECT code FROM roles WHERE id = :role_id AND deleted_at IS NULL
    """), {"role_id": user.role_id}).fetchone()
    return role[0] if role else ""


# ==================== 1. 导入补货建议Excel ====================

@router.post("/import")
async def import_inventory(
    file: Optional[UploadFile] = File(None),
    file_path: Optional[str] = Query(None, description="Excel文件路径（与file二选一）"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    导入补货建议Excel文件（后台异步执行）
    支持文件上传或指定文件路径两种方式
    导入完成后自动计算补货决策
    """
    try:
        from services.inventory_import_service import start_import_async

        # 优先使用上传的文件，其次使用文件路径
        if file:
            content = await file.read()
            result = start_import_async(file_content=content, filename=file.filename, tenant_id=current_user.tenant_id)
        elif file_path:
            if not os.path.exists(file_path):
                raise HTTPException(status_code=400, detail=f"文件不存在: {file_path}")
            result = start_import_async(file_path=file_path, tenant_id=current_user.tenant_id)
        else:
            raise HTTPException(status_code=400, detail="请提供 file 或 file_path 参数")

        return {"success": True, "data": result}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"启动导入任务失败: {e}")
        raise HTTPException(status_code=500, detail=f"启动导入任务失败: {str(e)}")


@router.get("/import-status")
async def get_import_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取导入任务状态
    用于轮询导入进度
    """
    try:
        from services.inventory_import_service import get_import_status
        return {"success": True, "data": get_import_status()}
    except Exception as e:
        logger.error(f"获取导入状态失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取导入状态失败: {str(e)}")


# ==================== 2. 触发补货决策计算（异步） ====================

@router.post("/calculate")
async def calculate_replenishment_async(
    snapshot_date: Optional[str] = Query(None, description="快照日期，格式YYYY-MM-DD，默认最新"),
    snapshot_ids: Optional[str] = Query(None, description="快照ID列表，逗号分隔，不传则全量计算"),
    current_user: User = Depends(get_current_user),
):
    """
    触发补货决策计算（后台异步执行）
    返回 task_id 用于轮询计算状态
    """
    try:
        from services.calculate_service import start_calculation_async

        ids_list = None
        if snapshot_ids:
            ids_list = [int(x.strip()) for x in snapshot_ids.split(",") if x.strip()]

        result = start_calculation_async(snapshot_date=snapshot_date, snapshot_ids=ids_list, tenant_id=current_user.tenant_id)
        return {"success": True, "data": result}

    except Exception as e:
        logger.error(f"启动补货计算失败: {e}")
        raise HTTPException(status_code=500, detail=f"启动补货计算失败: {str(e)}")


@router.get("/calculate/status/{task_id}")
async def get_calculation_status(
    task_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    获取计算任务状态
    用于轮询计算进度
    """
    try:
        from services.calculate_service import get_calculation_status

        status = get_calculation_status(task_id)
        if status is None:
            raise HTTPException(status_code=404, detail="任务不存在或已过期")

        return {"success": True, "data": status}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取计算状态失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取计算状态失败: {str(e)}")


# ==================== 3. 获取库存概览统计 ====================

@router.get("/overview")
async def get_inventory_overview(
    keyword: Optional[str] = Query(None, description="搜索关键词（与 /search 同口径）"),
    account: Optional[List[str]] = Query(None, description="店铺账号（与 /search 同口径）"),
    country: Optional[List[str]] = Query(None, description="国家/站点（与 /search 同口径）"),
    holiday_filter: Optional[str] = Query(None, description="节日筛选: all/only_holiday/only_non_holiday"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取库存概览统计

    **与列表同口径**：接受与 /search 相同的筛选参数，面板数字随筛选变化，
    保证「总SKU数」= 表格该筛选下的条数。

    包含各风险等级数量、快照日期、断货TOP10、冗余库存TOP10
    """
    try:
        from services.inventory_service import get_inventory_overview

        result = get_inventory_overview(
            db, tenant_id=current_user.tenant_id, user_id=current_user.id,
            user_role=get_user_role_code(current_user, db),
            keyword=keyword, holiday_filter=holiday_filter,
            account=account, country=country,
        )
        return {"success": True, "data": result}

    except Exception as e:
        logger.error(f"获取库存概览失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取库存概览失败: {str(e)}")


# ==================== 4. 搜索库存数据 ====================

@router.get("/search")
async def search_inventory(
    request: Request,
    keyword: Optional[str] = Query(None, description="搜索关键词（ASIN/商品名）"),
    risk_level: Optional[List[str]] = Query(None, description="风险等级: red/yellow/green"),
    replenishment_status: Optional[str] = Query(None, description="补货状态"),
    account: Optional[List[str]] = Query(None, description="店铺账号"),
    country: Optional[List[str]] = Query(None, description="国家/站点"),
    holiday_filter: Optional[str] = Query(None, description="节日筛选: only_holiday/only_non_holiday/all"),
    sort_field: Optional[str] = Query(None, description="排序字段"),
    sort_order: Optional[str] = Query(None, description="排序方式: asc/desc"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=500, description="每页数量"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    搜索库存数据
    支持按关键词、风险等级、补货状态、账号、国家、节日筛选，分页返回，支持排序
    """
    try:
        from services.inventory_service import search_inventory

        # 处理风险等级参数，兼容两种格式：risk_level 和 risk_level[]
        final_risk_level = risk_level
        if not final_risk_level:
            # 检查是否有 risk_level[] 参数
            query_params = request.query_params
            risk_level_params = query_params.getlist("risk_level") or query_params.getlist("risk_level[]")
            if risk_level_params and len(risk_level_params) > 0:
                final_risk_level = risk_level_params

        result = search_inventory(
            db,
            tenant_id=current_user.tenant_id,
            keyword=keyword,
            risk_level=final_risk_level,
            replenishment_status=replenishment_status,
            account=account,
            country=country,
            holiday_filter=holiday_filter,
            sort_field=sort_field,
            sort_order=sort_order,
            page=page,
            page_size=page_size,
            user_id=current_user.id,
            user_role=get_user_role_code(current_user, db)
        )
        return {"success": True, "data": result}

    except Exception as e:
        logger.error(f"搜索库存数据失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"搜索库存数据失败: {str(e)}")


# ==================== 5. 断货风险TOP10 ====================

@router.get("/stockout-top10")
async def get_stockout_top10(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取断货风险最高的10个SKU
    按预计断货天数升序排列
    """
    try:
        from services.inventory_service import get_stockout_top10

        result = get_stockout_top10(db, tenant_id=current_user.tenant_id, user_id=current_user.id, user_role=get_user_role_code(current_user, db))
        return {"success": True, "data": result}

    except Exception as e:
        logger.error(f"获取断货TOP10失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取断货TOP10失败: {str(e)}")


# ==================== 6. 冗余库存TOP10 ====================

@router.get("/overstock-top10")
async def get_overstock_top10(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取冗余库存最高的10个SKU
    按冗余天数降序排列
    """
    try:
        from services.inventory_service import get_overstock_top10

        result = get_overstock_top10(db, tenant_id=current_user.tenant_id, user_id=current_user.id, user_role=get_user_role_code(current_user, db))
        return {"success": True, "data": result}

    except Exception as e:
        logger.error(f"获取冗余库存TOP10失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取冗余库存TOP10失败: {str(e)}")


# ==================== 7. 在途货件详情 ====================

@router.get("/inbound-details")
async def get_inbound_details(
    asin: str = Query(..., description="ASIN（必填）"),
    account: Optional[str] = Query(None, description="店铺账号"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    查询指定ASIN的在途货件详情
    返回所有相关的在途 shipment 信息
    """
    try:
        from services.inventory_service import get_inbound_details

        result = get_inbound_details(db, tenant_id=current_user.tenant_id, asin=asin, account=account)
        return {"success": True, "data": result}

    except Exception as e:
        logger.error(f"获取在途货件详情失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取在途货件详情失败: {str(e)}")


# ==================== 8. 获取最新快照日期 ====================

@router.get("/latest-date")
async def get_latest_snapshot_date(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    获取最新的库存快照日期
    用于前端展示当前数据的时间范围
    """
    try:
        from services.inventory_service import get_latest_snapshot_date

        snapshot_date = get_latest_snapshot_date(db, tenant_id=current_user.tenant_id)
        return {"success": True, "data": {"snapshot_date": snapshot_date}}

    except Exception as e:
        logger.error(f"获取最新快照日期失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取最新快照日期失败: {str(e)}")


# ==================== 9. 获取筛选选项 ====================

@router.get("/filter-options")
async def get_filter_options(
    country: Optional[str] = Query(None, description="已选中的国家，用于过滤店铺"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取当前用户可见的店铺和国家筛选选项
    使用 inventory_name 映射后的店铺名
    国家从库存表中提取（更完整），而不是只用 stores.site
    支持国家-店铺联动筛选：传入country参数时，只返回属于该国家的店铺
    """
    from models.store import Store
    from models.restock import InventorySnapshot
    from sqlalchemy import text, or_

    # 获取用户可见的店铺
    if is_admin_user(current_user, db):
        user_store_ids = None
    else:
        user_stores = db.execute(
            text("SELECT store_id FROM user_stores WHERE user_id = :uid AND tenant_id = :tid"),
            {"uid": current_user.id, "tid": current_user.tenant_id}
        ).fetchall()
        user_store_ids = [s[0] for s in user_stores if s[0]]

    # 查询店铺
    query = db.query(Store).filter(
        Store.tenant_id == current_user.tenant_id,
        Store.status == "active"
    )

    # 如果传入了国家，只返回属于该国家的店铺
    if country:
        query = query.filter(Store.site == country)

    if user_store_ids:
        query = query.filter(Store.id.in_(user_store_ids))

    stores = query.all()

    # 构建映射后的店铺列表
    store_options = []

    for store in stores:
        # 使用 inventory_name 映射（inventory_name 已包含站点信息）
        if store.inventory_name:
            inventory_account = store.inventory_name
        else:
            inventory_account = f"{store.name}-{store.site}"

        store_options.append({
            "value": inventory_account,
            "label": inventory_account
        })

    # 从库存表中提取所有国家（拆分集合值如"美国、英国"）
    countries = set()

    if is_admin_user(current_user, db):
        # admin用户：显示所有国家
        country_records = db.query(InventorySnapshot.country).filter(
            InventorySnapshot.country.isnot(None),
            InventorySnapshot.country != ""
        ).distinct().all()
        for record in country_records:
            if record[0]:
                for c in record[0].split('、'):
                    c = c.strip()
                    if c:
                        countries.add(c)
    else:
        # 非admin用户：只显示有权限店铺对应的国家
        # 从 stores 表获取用户有权限的店铺的 site 字段
        user_store_sites = db.query(Store.site).filter(
            Store.tenant_id == current_user.tenant_id,
            Store.status == "active",
            Store.id.in_(user_store_ids) if user_store_ids else False,
            Store.site.isnot(None),
            Store.site != ""
        ).distinct().all()

        # site 到中文国家名的映射
        site_to_country = {
            '美国': '美国', '英国': '英国', '德国': '德国', '法国': '法国',
            '意大利': '意大利', '西班牙': '西班牙', '日本': '日本',
            '加拿大': '加拿大', '墨西哥': '墨西哥', '澳大利亚': '澳大利亚',
            '荷兰': '荷兰', '瑞典': '瑞典', '波兰': '波兰', '比利时': '比利时',
            '爱尔兰': '爱尔兰', '新加坡': '新加坡', '阿联酋': '阿联酋',
            '印度': '印度', '巴西': '巴西', '土耳其': '土耳其',
            'US': '美国', 'USA': '美国', 'UK': '英国', 'DE': '德国',
            'FR': '法国', 'IT': '意大利', 'ES': '西班牙', 'JP': '日本',
            'CA': '加拿大', 'MX': '墨西哥', 'AU': '澳大利亚', 'NL': '荷兰',
            'SE': '瑞典', 'PL': '波兰', 'BE': '比利时', 'IE': '爱尔兰',
            'SG': '新加坡', 'AE': '阿联酋', 'IN': '印度', 'BR': '巴西',
            'TR': '土耳其',
        }
        for record in user_store_sites:
            if record[0]:
                country_name = site_to_country.get(record[0], record[0])
                if country_name:
                    countries.add(country_name)

    return {
        "stores": sorted(store_options, key=lambda x: x["label"]),
        "countries": sorted([{"value": c, "label": c} for c in countries], key=lambda x: x["label"])
    }


# ==================== 10. 导出库存数据 ====================

@router.get("/export")
async def export_inventory(
    request: Request,
    keyword: Optional[str] = Query(None),
    risk_level: Optional[List[str]] = Query(None),
    replenishment_status: Optional[str] = Query(None),
    account: Optional[List[str]] = Query(None),
    country: Optional[List[str]] = Query(None),
    sort_field: Optional[str] = Query(None),
    sort_order: Optional[str] = Query(None),
    fields: Optional[List[str]] = Query(None, description="导出字段列表，默认全部"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """导出库存数据为Excel"""
    from services.inventory_service import search_inventory, export_inventory_to_excel
    import io

    # 获取全部数据（不分页，最多5000条）
    result = search_inventory(
        db, tenant_id=current_user.tenant_id, keyword=keyword, risk_level=risk_level,
        replenishment_status=replenishment_status,
        account=account, country=country,
        sort_field=sort_field, sort_order=sort_order,
        page=1, page_size=5000,
        user_id=current_user.id, user_role=get_user_role_code(current_user, db)
    )

    items = result["items"]

    # 排序：根据可售天数升序排列（如果前端未指定排序）
    if not sort_field:
        items = sorted(items, key=lambda x: x.get("days_of_supply", 0), reverse=False)

    # 生成Excel
    output = export_inventory_to_excel(items, fields)

    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        io.BytesIO(output.getvalue()),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=inventory_export.xlsx"}
    )


# ==================== 10. 同步飞书FBA在途数据 ====================

@router.post("/sync-feishu-inbound")
async def sync_feishu_inbound(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    启动飞书FBA在途数据同步（异步）
    """
    try:
        from services.feishu_sync_service import start_sync_async

        result = start_sync_async()
        return {"success": True, "data": result}

    except Exception as e:
        logger.error(f"启动飞书同步失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"启动失败: {str(e)}")


@router.get("/sync-feishu-status")
async def get_sync_feishu_status(
    current_user: User = Depends(get_current_user)
):
    """
    获取飞书FBA在途数据同步状态
    """
    try:
        from services.feishu_sync_service import get_sync_status

        status = get_sync_status()
        return {"success": True, "data": status}

    except Exception as e:
        logger.error(f"获取同步状态失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取状态失败: {str(e)}")


# ==================== 10.1 同步飞书海外仓库存数据 ====================

@router.post("/sync-overseas")
async def sync_overseas_inventory(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    启动飞书海外仓库存同步（异步）

    同步规则：源表为按日快照，按 (SKU, SKU名称) 去重取最新日期后，
    覆盖写入 overseas_inventories，并回写最新库存快照的主行。
    """
    try:
        from services.overseas_inventory_service import start_sync_async

        result = start_sync_async(tenant_id=current_user.tenant_id)
        return {"success": True, "data": result}

    except Exception as e:
        logger.error(f"启动海外仓同步失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"启动失败: {str(e)}")


@router.get("/sync-overseas-status")
async def get_sync_overseas_status(
    current_user: User = Depends(get_current_user)
):
    """获取飞书海外仓库存同步状态"""
    try:
        from services.overseas_inventory_service import get_sync_status

        return {"success": True, "data": get_sync_status()}

    except Exception as e:
        logger.error(f"获取海外仓同步状态失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取状态失败: {str(e)}")


@router.get("/overseas-summary")
async def get_overseas_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """海外仓库存汇总（已同步 SKU 数 / 总可售 / 数据日期）"""
    try:
        from services.overseas_inventory_service import get_overseas_summary as _summary

        return {"success": True, "data": _summary(db, current_user.tenant_id)}

    except Exception as e:
        logger.error(f"获取海外仓汇总失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取汇总失败: {str(e)}")


# ==================== 11. 更新查验货件数量 ====================

@router.put("/inspection-quantity")
async def update_inspection_quantity(
    snapshot_id: int = Query(..., description="快照ID"),
    inspection_quantity: int = Query(..., ge=0, description="查验货件数量"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """更新查验货件数量，并同步重算该快照的补货决策"""
    from models.restock import InventorySnapshot

    snap = db.query(InventorySnapshot).filter(
        InventorySnapshot.id == snapshot_id,
        InventorySnapshot.tenant_id == current_user.tenant_id,
        InventorySnapshot.deleted_at.is_(None)
    ).first()
    if not snap:
        raise HTTPException(status_code=404, detail="快照记录不存在")

    snap.inspection_quantity = inspection_quantity
    snap.total_stock = (snap.fba_stock or 0) + (snap.fba_inbound or 0) + (snap.local_inventory or 0) - inspection_quantity
    db.commit()

    # 同步重算该快照的补货决策（days_of_supply / stockout_date / risk_level / suggest_qty 等）
    try:
        from services.inventory_service import calculate_replenishment
        calc_result = calculate_replenishment(
            db,
            snapshot_date=snap.snapshot_date.isoformat() if snap.snapshot_date else None,
            snapshot_ids=[snapshot_id],
            tenant_id=current_user.tenant_id,
        )
        logger.info(f"查验数量更新后重算补货决策 snapshot_id={snapshot_id}: {calc_result}")
    except Exception as calc_e:
        logger.error(f"查验数量更新后重算补货决策失败 snapshot_id={snapshot_id}: {calc_e}", exc_info=True)

    return {"success": True, "data": {"inspection_quantity": inspection_quantity, "total_stock": snap.total_stock}}


# ==================== 12. 获取汇总行子行数据 ====================

@router.get("/summary-children")
async def get_summary_children(
    asin: str = Query(..., description="汇总行的ASIN"),
    account: Optional[str] = Query(None, description="汇总行的account字段（店铺列表，用于过滤子行，避免同ASIN多汇总行时子行串扰）"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取汇总行的子行（共享库存）数据"""
    try:
        from services.inventory_service import get_summary_children as get_children
        children = get_children(db, tenant_id=current_user.tenant_id, asin=asin, parent_account=account)
        return {"success": True, "data": children}
    except Exception as e:
        logger.error(f"获取汇总行子行数据失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取汇总行子行数据失败: {str(e)}")


# ==================== 13. 标记/取消节日产品 ====================

@router.post("/mark-holiday")
async def mark_holiday(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    批量标记或取消节日产品
    请求体: {"snapshot_ids": [1,2,3], "is_holiday": true}
    如果是汇总行(summary_flag="是")，同时标记其所有子行(共享库存)
    """
    from models.restock import InventorySnapshot

    body = await request.json()
    snapshot_ids = body.get("snapshot_ids", [])
    is_holiday = body.get("is_holiday", True)

    if not snapshot_ids:
        raise HTTPException(status_code=400, detail="snapshot_ids不能为空")

    # 兼容旧接口：is_holiday 字段已改为 VARCHAR(20) 存储节日类型
    # 布尔值 True → "其他"，False → ""
    holiday_value = "其他" if is_holiday else ""

    # 查询这些快照，找出汇总行
    snapshots = db.query(InventorySnapshot).filter(
        InventorySnapshot.id.in_(snapshot_ids),
        InventorySnapshot.tenant_id == current_user.tenant_id,
        InventorySnapshot.deleted_at.is_(None)
    ).all()

    all_ids = set(snapshot_ids)
    for snap in snapshots:
        # 如果是汇总行，找到其子行（共享库存）
        if snap.summary_flag == "是" and snap.asin:
            children = db.query(InventorySnapshot).filter(
                InventorySnapshot.summary_flag == "共享库存",
                InventorySnapshot.asin == snap.asin,
                InventorySnapshot.tenant_id == current_user.tenant_id,
                InventorySnapshot.deleted_at.is_(None)
            ).all()
            for child in children:
                all_ids.add(child.id)

    # 批量更新
    db.query(InventorySnapshot).filter(
        InventorySnapshot.id.in_(list(all_ids)),
        InventorySnapshot.tenant_id == current_user.tenant_id,
        InventorySnapshot.deleted_at.is_(None)
    ).update({InventorySnapshot.is_holiday: holiday_value}, synchronize_session=False)

    db.commit()
    return {
        "success": True,
        "data": {
            "updated_count": len(all_ids),
            "is_holiday": is_holiday
        }
    }


@router.post("/mark-product-status")
async def mark_product_status(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    批量标记产品状态（节日/停售/清除标记）
    请求体: {"snapshot_ids": [1,2,3], "action": "holiday"|"discontinued"|"clear", "holiday_type": "圣诞"}
    - action="holiday": 设置 is_holiday=holiday_type（必传），is_discontinued=False
    - action="discontinued": 设置 is_discontinued=True，is_holiday=""
    - action="clear": 设置 is_holiday=""，is_discontinued=False
    如果是汇总行(summary_flag="是")，同时更新其所有子行(共享库存)
    同时更新 inventory_snapshots 和 replenishment_decisions 两张表
    """
    from models.restock import InventorySnapshot, ReplenishmentDecision

    body = await request.json()
    snapshot_ids = body.get("snapshot_ids", [])
    action = body.get("action")
    holiday_type = body.get("holiday_type", "")

    if not snapshot_ids:
        raise HTTPException(status_code=400, detail="snapshot_ids不能为空")

    if action not in ("holiday", "discontinued", "clear"):
        raise HTTPException(status_code=400, detail="action必须为 holiday/discontinued/clear 之一")

    if action == "holiday" and not holiday_type:
        raise HTTPException(status_code=400, detail="action=holiday时必须传holiday_type")

    # 根据action确定更新值
    if action == "holiday":
        new_holiday = holiday_type
        new_discontinued = False
    elif action == "discontinued":
        new_holiday = ""
        new_discontinued = True
    else:  # clear
        new_holiday = ""
        new_discontinued = False

    # 查询这些快照，找出汇总行
    snapshots = db.query(InventorySnapshot).filter(
        InventorySnapshot.id.in_(snapshot_ids),
        InventorySnapshot.tenant_id == current_user.tenant_id,
        InventorySnapshot.deleted_at.is_(None)
    ).all()

    all_ids = set(snapshot_ids)
    for snap in snapshots:
        # 如果是汇总行，找到其子行（共享库存）
        if snap.summary_flag == "是" and snap.asin:
            children = db.query(InventorySnapshot).filter(
                InventorySnapshot.summary_flag == "共享库存",
                InventorySnapshot.asin == snap.asin,
                InventorySnapshot.tenant_id == current_user.tenant_id,
                InventorySnapshot.deleted_at.is_(None)
            ).all()
            for child in children:
                all_ids.add(child.id)

    all_ids_list = list(all_ids)

    # 更新 inventory_snapshots 表
    db.query(InventorySnapshot).filter(
        InventorySnapshot.id.in_(all_ids_list),
        InventorySnapshot.tenant_id == current_user.tenant_id,
        InventorySnapshot.deleted_at.is_(None)
    ).update({
        InventorySnapshot.is_holiday: new_holiday,
        InventorySnapshot.is_discontinued: new_discontinued
    }, synchronize_session=False)

    # 更新 replenishment_decisions 表（通过 snapshot_id 关联）
    db.query(ReplenishmentDecision).filter(
        ReplenishmentDecision.snapshot_id.in_(all_ids_list),
        ReplenishmentDecision.tenant_id == current_user.tenant_id,
        ReplenishmentDecision.deleted_at.is_(None)
    ).update({
        ReplenishmentDecision.is_holiday: new_holiday,
        ReplenishmentDecision.is_discontinued: new_discontinued
    }, synchronize_session=False)

    db.commit()

    # 标签系统：人工标记变更后刷新决策行展示口径（filter_status/force_replenish，K2/K3）
    # **只重算本次被标记的行**（all_ids_list 已含汇总行展开出的共享库存子行）——
    # 全量重算在 5.9 万决策行上约 2000ms，是「点标记很慢」的主因。
    try:
        from services.tag_service import apply_tags_to_decisions
        apply_tags_to_decisions(db, current_user.tenant_id, snapshot_ids=all_ids_list)
    except Exception as e:
        logger.warning(f"人工标记后刷新过滤口径失败（不影响标记本身）: {e}")

    # 取消标记还必须清 **product_tags 里的节日标签**：快照列与标签表是两处数据源，
    # 自动打标写入的节日只存在于 product_tags —— 只清快照列时界面仍显示节日，
    # 表现为「取消标记去不掉」。按每行 (asin, account) 调 suppress（通配 ∪ 本行）。
    suppressed = 0
    if action == "clear":
        try:
            from services.tag_service import suppress_holiday_tags
            rows = db.query(InventorySnapshot.asin, InventorySnapshot.account).filter(
                InventorySnapshot.id.in_(all_ids_list),
                InventorySnapshot.tenant_id == current_user.tenant_id,
            ).all()
            for _asin, _acct in {(r[0], r[1] or "") for r in rows}:
                res = suppress_holiday_tags(
                    db, current_user.tenant_id, _asin, _acct,
                    operator=current_user.username or f"user_{current_user.id}",
                    reason="界面取消标记",
                )
                suppressed += int(res.get("suppressed") or 0)
        except Exception as e:
            logger.warning(f"取消标记时清除节日标签失败（不影响快照标记）: {e}")

    return {
        "success": True,
        "data": {
            "updated_count": len(all_ids_list),
            "suppressed_tags": suppressed,
        }
    }


# ==================== 14. 分货计算 ====================

class AllocateItem(BaseModel):
    asin: Optional[str] = None
    sku: Optional[str] = None
    country: str
    purchase_qty: int

class AllocateRequest(BaseModel):
    items: List[AllocateItem]

class AllocateResult(BaseModel):
    asin: str
    sku: str
    country: str
    purchase_qty: int
    sales_30d: float
    spot_qty: float
    inbound_qty: float
    judgment: str
    red_qty: int
    sea_qty: int


@router.post("/allocate-shipment")
async def allocate_shipment(
    request: AllocateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """分货计算接口 - 根据SKU、国家、进货数量计算红单/海运分货结果"""
    try:
        from services.inventory_service import allocate_shipment as allocate_service
        results = allocate_service(
            db,
            tenant_id=current_user.tenant_id,
            items=[item.dict() for item in request.items],
            user_id=current_user.id,
            user_role=get_user_role_code(current_user, db)
        )
        return {"success": True, "data": results}
    except Exception as e:
        logger.error(f"分货计算失败: {e}")
        raise HTTPException(status_code=500, detail=f"分货计算失败: {str(e)}")

