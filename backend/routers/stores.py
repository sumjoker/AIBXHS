from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text
from database.database import get_db
from dependencies import get_current_user, get_current_admin_user
from models.user import User

router = APIRouter(prefix="/api/stores", tags=["stores"])


class StoreCreate(BaseModel):
    name: str
    platform: str = "amazon"
    site: Optional[str] = None
    department_id: Optional[int] = None


class StoreUpdate(BaseModel):
    name: Optional[str] = None
    platform: Optional[str] = None
    site: Optional[str] = None
    department_id: Optional[int] = None
    status: Optional[str] = None


@router.get("/")
async def get_stores(
    page: int = 1,
    page_size: int = 20,
    name_search: Optional[str] = None,
    site_search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        where_conditions = ["s.tenant_id = :tenant_id"]
        params = {"tenant_id": current_user.tenant_id}

        if current_user.role != "admin":
            dept_ids = db.execute(
                text("SELECT department_id FROM user_departments WHERE user_id = :uid"),
                {"uid": current_user.id}
            ).fetchall()
            dept_id_list = [d[0] for d in dept_ids]
            if dept_id_list:
                dept_placeholders = ",".join([f":dept_{i}" for i in range(len(dept_id_list))])
                for i, did in enumerate(dept_id_list):
                    params[f"dept_{i}"] = did
                where_conditions.append(f"s.department_id IN ({dept_placeholders})")
            else:
                where_conditions.append("1=0")

        if name_search:
            where_conditions.append("s.name LIKE :name_search")
            params["name_search"] = f"%{name_search}%"
        
        if site_search:
            where_conditions.append("s.site LIKE :site_search")
            params["site_search"] = f"%{site_search}%"

        where_clause = " AND ".join(where_conditions)
        
        # 计算总数
        count_query = text(f"""
            SELECT COUNT(*) FROM stores s
            LEFT JOIN departments d ON s.department_id = d.id
            WHERE {where_clause}
        """)
        total_result = db.execute(count_query, params)
        total = total_result.fetchone()[0]
        
        # 分页查询数据
        offset = (page - 1) * page_size
        params["offset"] = offset
        params["page_size"] = page_size
        
        query = text(f"""
            SELECT s.id, s.name, s.platform, s.site, s.status, 
                   s.department_id, d.name as department_name, s.inventory_name, s.created_at
            FROM stores s
            LEFT JOIN departments d ON s.department_id = d.id
            WHERE {where_clause}
            ORDER BY 
                CASE WHEN s.department_id IS NOT NULL THEN 0 ELSE 1 END,
                s.name ASC,
                s.site ASC
            LIMIT :page_size OFFSET :offset
        """)
        result = db.execute(query, params)
        stores = []
        for row in result:
            stores.append({
                "id": row[0],
                "name": row[1],
                "platform": row[2],
                "site": row[3] or "",
                "status": row[4],
                "department_id": row[5],
                "department_name": row[6] or "未分配",
                "inventory_name": row[7] or "",
                "created_at": row[8].strftime("%Y-%m-%d %H:%M:%S") if row[8] else "",
            })
        return {"success": True, "data": stores, "total": total}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取店铺列表失败: {str(e)}")


@router.post("/")
async def create_store(
    store_data: StoreCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    try:
        insert_sql = text("""
            INSERT INTO stores (tenant_id, name, platform, site, department_id)
            VALUES (:tenant_id, :name, :platform, :site, :department_id)
        """)
        result = db.execute(insert_sql, {
            "tenant_id": current_user.tenant_id,
            "name": store_data.name,
            "platform": store_data.platform,
            "site": store_data.site,
            "department_id": store_data.department_id,
        })
        db.commit()
        return {
            "success": True,
            "message": "店铺创建成功",
            "data": {"id": result.lastrowid}
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"创建店铺失败: {str(e)}")


@router.put("/{store_id}")
async def update_store(
    store_id: int,
    store_data: StoreUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    try:
        check = text("SELECT id FROM stores WHERE id = :id AND tenant_id = :tid")
        row = db.execute(check, {"id": store_id, "tid": current_user.tenant_id}).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="店铺不存在")

        updates = []
        params = {"id": store_id}
        if store_data.name is not None:
            updates.append("name = :name")
            params["name"] = store_data.name
        if store_data.platform is not None:
            updates.append("platform = :platform")
            params["platform"] = store_data.platform
        if store_data.site is not None:
            updates.append("site = :site")
            params["site"] = store_data.site
        if store_data.department_id is not None:
            updates.append("department_id = :department_id")
            params["department_id"] = store_data.department_id
        if store_data.status is not None:
            updates.append("status = :status")
            params["status"] = store_data.status

        if updates:
            update_sql = text(f"UPDATE stores SET {', '.join(updates)}, updated_at = NOW() WHERE id = :id")
            db.execute(update_sql, params)
            db.commit()

        return {"success": True, "message": "店铺更新成功"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"更新店铺失败: {str(e)}")


@router.delete("/{store_id}")
async def delete_store(
    store_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    try:
        db.execute(text("DELETE FROM stores WHERE id = :id AND tenant_id = :tid"), {
            "id": store_id,
            "tid": current_user.tenant_id
        })
        db.commit()
        return {"success": True, "message": "店铺删除成功"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"删除店铺失败: {str(e)}")


class BatchUpdateDepartmentRequest(BaseModel):
    store_ids: List[int]
    department_id: Optional[int] = None


@router.post("/batch-update-department")
async def batch_update_department(
    request: BatchUpdateDepartmentRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    try:
        if not request.store_ids:
            raise HTTPException(status_code=400, detail="请选择要更新的店铺")
        
        # 验证所有店铺都属于当前租户
        placeholders = ",".join([f":id_{i}" for i in range(len(request.store_ids))])
        params = {f"id_{i}": store_id for i, store_id in enumerate(request.store_ids)}
        params["tenant_id"] = current_user.tenant_id
        
        check_query = text(f"""
            SELECT COUNT(*) FROM stores 
            WHERE id IN ({placeholders}) AND tenant_id = :tenant_id
        """)
        count_result = db.execute(check_query, params)
        count = count_result.fetchone()[0]
        
        if count != len(request.store_ids):
            raise HTTPException(status_code=400, detail="部分店铺不存在或无权限")
        
        # 批量更新
        update_params = params.copy()
        update_params["department_id"] = request.department_id
        
        update_query = text(f"""
            UPDATE stores 
            SET department_id = :department_id, updated_at = NOW()
            WHERE id IN ({placeholders}) AND tenant_id = :tenant_id
        """)
        db.execute(update_query, update_params)
        db.commit()
        
        return {"success": True, "message": f"成功更新 {count} 个店铺的部门"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"批量更新失败: {str(e)}")
