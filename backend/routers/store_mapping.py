"""
店铺映射管理API
用于管理数据库店铺名与库存数据店铺名的映射关系
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text
from database.database import get_db
from dependencies import get_current_user, get_current_admin_user
from models.user import User
from models.store import Store
from utils.store_mapping import (
    get_all_store_mappings,
    auto_update_store_inventory_names,
    get_inventory_account,
    STORE_MAPPING_RULES
)

router = APIRouter(prefix="/store-mapping", tags=["store-mapping"])


class StoreMappingUpdate(BaseModel):
    inventory_name: Optional[str] = None


class StoreMappingBatchUpdate(BaseModel):
    mappings: List[dict]  # [{"store_id": 1, "inventory_name": "JeVenis-US"}]


@router.get("/list")
async def get_store_mapping_list(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取所有店铺映射关系"""
    try:
        mappings = get_all_store_mappings(db, tenant_id=current_user.tenant_id)
        return {"success": True, "data": mappings}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取店铺映射失败: {str(e)}")


@router.get("/rules")
async def get_mapping_rules(
    current_user: User = Depends(get_current_user)
):
    """获取预定义的映射规则"""
    rules = []
    for (pattern, site), inventory_name in STORE_MAPPING_RULES.items():
        rules.append({
            "pattern": pattern,
            "site": site,
            "inventory_name": inventory_name,
            "inventory_account": f"{inventory_name}-{site}"
        })
    return {"success": True, "data": rules}


@router.post("/auto-update")
async def auto_update_mappings(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    """自动根据规则更新所有店铺的 inventory_name"""
    try:
        result = auto_update_store_inventory_names(db, tenant_id=current_user.tenant_id)
        return {
            "success": True,
            "message": f"自动更新完成: 共 {result['total']} 个店铺, 更新 {result['updated']} 个, 跳过 {result['skipped']} 个",
            "data": result
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"自动更新失败: {str(e)}")


@router.put("/{store_id}")
async def update_store_mapping(
    store_id: int,
    data: StoreMappingUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    """手动更新单个店铺的 inventory_name"""
    try:
        store = db.query(Store).filter(
            Store.id == store_id,
            Store.tenant_id == current_user.tenant_id
        ).first()

        if not store:
            raise HTTPException(status_code=404, detail="店铺不存在")

        store.inventory_name = data.inventory_name
        db.commit()

        return {
            "success": True,
            "message": "更新成功",
            "data": {
                "store_id": store.id,
                "store_name": store.name,
                "site": store.site,
                "inventory_name": store.inventory_name,
                "inventory_account": f"{store.inventory_name}-{store.site}" if store.inventory_name else None
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"更新失败: {str(e)}")


@router.post("/batch-update")
async def batch_update_mappings(
    data: StoreMappingBatchUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    """批量更新店铺映射"""
    try:
        updated_count = 0
        errors = []

        for item in data.mappings:
            store_id = item.get("store_id")
            inventory_name = item.get("inventory_name")

            store = db.query(Store).filter(
                Store.id == store_id,
                Store.tenant_id == current_user.tenant_id
            ).first()

            if store:
                store.inventory_name = inventory_name
                updated_count += 1
            else:
                errors.append(f"店铺ID {store_id} 不存在")

        db.commit()

        return {
            "success": True,
            "message": f"批量更新完成: 成功 {updated_count} 个, 失败 {len(errors)} 个",
            "data": {"updated": updated_count, "errors": errors}
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"批量更新失败: {str(e)}")


@router.get("/preview/{store_id}")
async def preview_store_mapping(
    store_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """预览单个店铺的映射结果"""
    try:
        store = db.query(Store).filter(
            Store.id == store_id,
            Store.tenant_id == current_user.tenant_id
        ).first()

        if not store:
            raise HTTPException(status_code=404, detail="店铺不存在")

        # 计算映射后的库存账号
        inventory_account = get_inventory_account(store.name, store.site or "")

        return {
            "success": True,
            "data": {
                "store_id": store.id,
                "store_name": store.name,
                "site": store.site,
                "current_inventory_name": store.inventory_name,
                "suggested_inventory_name": inventory_account.split("-")[0] if "-" in inventory_account else inventory_account,
                "inventory_account": inventory_account
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"预览失败: {str(e)}")


@router.get("/filter-options")
async def get_store_filter_options(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取店铺筛选选项（使用 inventory_name 映射后的值）
    用于库存查询时的店铺筛选
    """
    try:
        from utils.store_mapping import get_store_mapping_from_db

        # 获取映射后的店铺列表
        mapping = get_store_mapping_from_db(db, tenant_id=current_user.tenant_id)

        # 构建筛选选项
        stores = []
        sites = set()

        for inventory_account, store in mapping.items():
            stores.append({
                "value": inventory_account,
                "label": f"{store.name} ({inventory_account})"
            })
            if store.site:
                sites.add(store.site)

        # 如果没有设置 inventory_name，使用原始店铺名
        if not stores:
            stores_query = db.query(Store).filter(
                Store.tenant_id == current_user.tenant_id,
                Store.status == "active"
            ).all()

            for store in stores_query:
                account = f"{store.name}-{store.site}" if store.site else store.name
                stores.append({
                    "value": account,
                    "label": account
                })
                if store.site:
                    sites.add(store.site)

        return {
            "success": True,
            "data": {
                "stores": sorted(stores, key=lambda x: x["label"]),
                "countries": sorted([{"value": s, "label": s} for s in sites], key=lambda x: x["label"])
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取筛选选项失败: {str(e)}")
