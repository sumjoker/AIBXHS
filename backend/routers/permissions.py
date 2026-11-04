from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text, and_
from datetime import datetime

from database.database import get_db
from dependencies import get_current_user, get_current_admin_user, get_user_permission_codes
from models.user import User
from models.permission import Role, Permission, RolePermission

router = APIRouter(prefix="/api/permissions", tags=["permissions"])


# ============== 角色管理 ==============

class RoleCreate(BaseModel):
    name: str
    code: str
    description: Optional[str] = None
    sort_order: Optional[int] = 0


class RoleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    sort_order: Optional[int] = None


@router.get("/roles")
async def get_roles(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取角色列表"""
    roles = db.execute(text("""
        SELECT id, name, code, description, is_system, sort_order, created_at
        FROM roles
        WHERE tenant_id = :tenant_id AND deleted_at IS NULL
        ORDER BY sort_order ASC, id ASC
    """), {"tenant_id": current_user.tenant_id}).fetchall()
    
    role_list = []
    for role in roles:
        # 获取角色用户数量（主要依赖 role_id 字段）
        user_count = db.execute(text("""
            SELECT COUNT(*) FROM users
            WHERE role_id = :role_id
            AND tenant_id = :tenant_id AND deleted_at IS NULL
        """), {"role_id": role[0], "tenant_id": current_user.tenant_id}).scalar() or 0
        
        role_list.append({
            "id": role[0],
            "name": role[1],
            "code": role[2],
            "description": role[3],
            "is_system": role[4],
            "sort_order": role[5],
            "created_at": role[6].isoformat() if role[6] else None,
            "user_count": user_count
        })
    
    return {"success": True, "data": role_list}


@router.post("/roles")
async def create_role(
    role_data: RoleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """创建角色"""
    # 清理输入的角色编码
    cleaned_code = clean_string(role_data.code) if role_data.code else ""
    if not cleaned_code:
        raise HTTPException(status_code=400, detail="角色编码不能为空")
    
    # 检查角色编码是否已存在
    existing = db.execute(text("""
        SELECT id FROM roles WHERE tenant_id = :tenant_id AND code = :code AND deleted_at IS NULL
    """), {"tenant_id": current_user.tenant_id, "code": cleaned_code}).fetchone()
    
    if existing:
        raise HTTPException(status_code=400, detail="角色编码已存在")
    
    # 创建角色
    result = db.execute(text("""
        INSERT INTO roles (tenant_id, name, code, description, is_system, sort_order, created_at, updated_at)
        VALUES (:tenant_id, :name, :code, :description, :is_system, :sort_order, NOW(), NOW())
    """), {
        "tenant_id": current_user.tenant_id,
        "name": role_data.name,
        "code": cleaned_code,
        "description": role_data.description,
        "is_system": False,
        "sort_order": role_data.sort_order or 0
    })
    db.commit()
    
    role_id = result.lastrowid
    return {"success": True, "message": "创建成功", "data": {"id": role_id}}


@router.put("/roles/{role_id}")
async def update_role(
    role_id: int,
    role_data: RoleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    """更新角色"""
    # 检查角色是否存在
    role = db.execute(text("""
        SELECT id, code FROM roles WHERE id = :id AND tenant_id = :tenant_id AND deleted_at IS NULL
    """), {"id": role_id, "tenant_id": current_user.tenant_id}).fetchone()
    
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    
    if role[1] == "admin":  # 管理员角色不可编辑
        raise HTTPException(status_code=400, detail="管理员角色不能修改")
    
    # 构建更新语句
    updates = []
    params = {"id": role_id}
    if role_data.name is not None:
        updates.append("name = :name")
        params["name"] = role_data.name
    if role_data.description is not None:
        updates.append("description = :description")
        params["description"] = role_data.description
    if role_data.sort_order is not None:
        updates.append("sort_order = :sort_order")
        params["sort_order"] = role_data.sort_order
    
    if updates:
        updates.append("updated_at = NOW()")
        db.execute(text(f"UPDATE roles SET {', '.join(updates)} WHERE id = :id"), params)
        db.commit()
    
    return {"success": True, "message": "更新成功"}


@router.delete("/roles/{role_id}")
async def delete_role(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    """删除角色"""
    role = db.execute(text("""
        SELECT id, code FROM roles WHERE id = :id AND tenant_id = :tenant_id AND deleted_at IS NULL
    """), {"id": role_id, "tenant_id": current_user.tenant_id}).fetchone()
    
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    
    if role[1] == "admin":  # 管理员角色不可删除
        raise HTTPException(status_code=400, detail="管理员角色不能删除")
    
    # 检查是否有用户使用该角色（同时兼容 role_id 和 role 字段）
    user_count = db.execute(text("""
        SELECT COUNT(*) FROM users
        WHERE (role_id = :role_id OR role = :role_code)
        AND tenant_id = :tenant_id AND deleted_at IS NULL
    """), {"role_id": role_id, "role_code": role[1], "tenant_id": current_user.tenant_id}).scalar() or 0
    
    if user_count > 0:
        raise HTTPException(status_code=400, detail=f"该角色已被 {user_count} 个用户使用，无法删除")
    
    # 软删除角色
    db.execute(text("UPDATE roles SET deleted_at = NOW() WHERE id = :id"), {"id": role_id})
    db.execute(text("UPDATE role_permissions SET deleted_at = NOW() WHERE role_id = :role_id"), {"role_id": role_id})
    db.commit()
    
    return {"success": True, "message": "删除成功"}


class RoleCopyRequest(BaseModel):
    name: str
    code: str
    description: Optional[str] = None


@router.post("/roles/{role_id}/copy")
async def copy_role(
    role_id: int,
    data: RoleCopyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    """复制角色（含权限，不含用户）"""
    # 检查源角色是否存在
    src_role = db.execute(text("""
        SELECT id, code FROM roles WHERE id = :id AND tenant_id = :tenant_id AND deleted_at IS NULL
    """), {"id": role_id, "tenant_id": current_user.tenant_id}).fetchone()
    if not src_role:
        raise HTTPException(status_code=404, detail="源角色不存在")

    # 清理输入
    cleaned_code = clean_string(data.code) if data.code else ""
    if not cleaned_code:
        raise HTTPException(status_code=400, detail="角色编码不能为空")

    # 检查角色编码是否已存在
    existing = db.execute(text("""
        SELECT id FROM roles WHERE tenant_id = :tenant_id AND code = :code AND deleted_at IS NULL
    """), {"tenant_id": current_user.tenant_id, "code": cleaned_code}).fetchone()
    if existing:
        raise HTTPException(status_code=400, detail="角色编码已存在")

    # 创建新角色
    result = db.execute(text("""
        INSERT INTO roles (tenant_id, name, code, description, is_system, sort_order, created_at, updated_at)
        VALUES (:tenant_id, :name, :code, :description, :is_system, :sort_order, NOW(), NOW())
    """), {
        "tenant_id": current_user.tenant_id,
        "name": data.name,
        "code": cleaned_code,
        "description": data.description,
        "is_system": False,
        "sort_order": 0,
    })
    new_role_id = result.lastrowid

    # 复制源角色的所有权限到新角色
    # 管理员角色默认拥有全部权限（role_permissions 无显式记录），复制时取该租户全部权限
    src_perms = db.execute(text("""
        SELECT permission_id FROM role_permissions
        WHERE role_id = :role_id AND tenant_id = :tenant_id AND deleted_at IS NULL
    """), {"role_id": role_id, "tenant_id": current_user.tenant_id}).fetchall()

    if not src_perms and src_role[1] == "admin":
        src_perms = db.execute(text("""
            SELECT id FROM permissions
            WHERE tenant_id = :tenant_id AND deleted_at IS NULL
        """), {"tenant_id": current_user.tenant_id}).fetchall()

    for perm in src_perms:
        perm_id = perm[0]
        # 检查是否已存在（含软删除的记录，恢复即可）
        existing_rp = db.execute(text("""
            SELECT id FROM role_permissions
            WHERE role_id = :role_id AND permission_id = :permission_id AND tenant_id = :tenant_id
        """), {
            "role_id": new_role_id,
            "permission_id": perm_id,
            "tenant_id": current_user.tenant_id,
        }).fetchone()
        if existing_rp:
            db.execute(text("""
                UPDATE role_permissions SET deleted_at = NULL, updated_at = NOW()
                WHERE role_id = :role_id AND permission_id = :permission_id AND tenant_id = :tenant_id
            """), {
                "role_id": new_role_id,
                "permission_id": perm_id,
                "tenant_id": current_user.tenant_id,
            })
        else:
            db.execute(text("""
                INSERT INTO role_permissions (tenant_id, role_id, permission_id, created_at, updated_at)
                VALUES (:tenant_id, :role_id, :permission_id, NOW(), NOW())
            """), {
                "tenant_id": current_user.tenant_id,
                "role_id": new_role_id,
                "permission_id": perm_id,
            })

    db.commit()
    return {"success": True, "message": "角色复制成功", "data": {"id": new_role_id}}


# ============== 权限管理 ==============

@router.get("/permissions")
async def get_permissions(
    type: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取权限列表"""
    where_clause = "tenant_id = :tenant_id AND deleted_at IS NULL"
    params = {"tenant_id": current_user.tenant_id}
    
    if type:
        where_clause += " AND type = :type"
        params["type"] = type
    
    permissions = db.execute(text(f"""
        SELECT id, name, code, type, module, parent_id, description, sort_order
        FROM permissions
        WHERE {where_clause}
        ORDER BY sort_order ASC, id ASC
    """), params).fetchall()
    
    perm_list = []
    for perm in permissions:
        perm_list.append({
            "id": perm[0],
            "name": perm[1],
            "code": perm[2],
            "type": perm[3],
            "module": perm[4],
            "parent_id": perm[5],
            "description": perm[6],
            "sort_order": perm[7]
        })
    
    return {"success": True, "data": perm_list}


@router.get("/roles/{role_id}/permissions")
async def get_role_permissions(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取角色的权限"""
    # 获取所有权限
    permissions = db.execute(text("""
        SELECT id, name, code, type, module, parent_id, sort_order
        FROM permissions
        WHERE tenant_id = :tenant_id AND deleted_at IS NULL
        ORDER BY sort_order ASC, id ASC
    """), {"tenant_id": current_user.tenant_id}).fetchall()
    
    # 获取角色已有的权限
    role_perms = db.execute(text("""
        SELECT permission_id FROM role_permissions
        WHERE role_id = :role_id AND deleted_at IS NULL
    """), {"role_id": role_id}).fetchall()
    
    role_perm_ids = {rp[0] for rp in role_perms}
    
    # 按模块分组
    modules = {}
    for perm in permissions:
        module = perm[4] or "其他"
        if module not in modules:
            modules[module] = []
        modules[module].append({
            "id": perm[0],
            "name": perm[1],
            "code": perm[2],
            "type": perm[3],
            "parent_id": perm[5],
            "selected": perm[0] in role_perm_ids
        })
    
    return {"success": True, "data": modules}


class RolePermissionsUpdate(BaseModel):
    permission_ids: List[int]


@router.put("/roles/{role_id}/permissions")
async def update_role_permissions(
    role_id: int,
    data: RolePermissionsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """更新角色权限"""
    # 检查角色是否存在
    role = db.execute(text("""
        SELECT id, code FROM roles WHERE id = :id AND tenant_id = :tenant_id AND deleted_at IS NULL
    """), {"id": role_id, "tenant_id": current_user.tenant_id}).fetchone()
    
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    
    # 管理员角色权限不可更改
    if role[1] == "admin":
        raise HTTPException(status_code=400, detail="管理员角色权限不可更改，默认拥有所有权限")
    
    try:
        # 先删除原有权限
        db.execute(text("UPDATE role_permissions SET deleted_at = NOW() WHERE role_id = :role_id AND tenant_id = :tenant_id"), {
            "role_id": role_id,
            "tenant_id": current_user.tenant_id
        })
        
        # 添加新权限
        for perm_id in data.permission_ids:
            # 检查权限是否存在且属于该租户
            perm = db.execute(text("""
                SELECT id FROM permissions WHERE id = :id AND tenant_id = :tenant_id AND deleted_at IS NULL
            """), {"id": perm_id, "tenant_id": current_user.tenant_id}).fetchone()
            
            if perm:
                # 先检查是否存在（软删除状态）
                existing = db.execute(text("""
                    SELECT id FROM role_permissions 
                    WHERE role_id = :role_id AND permission_id = :permission_id AND tenant_id = :tenant_id
                """), {
                    "role_id": role_id,
                    "permission_id": perm_id,
                    "tenant_id": current_user.tenant_id
                }).fetchone()
                
                if existing:
                    # 更新现有记录（软删除恢复）
                    db.execute(text("""
                        UPDATE role_permissions 
                        SET deleted_at = NULL, updated_at = NOW()
                        WHERE role_id = :role_id AND permission_id = :permission_id AND tenant_id = :tenant_id
                    """), {
                        "role_id": role_id,
                        "permission_id": perm_id,
                        "tenant_id": current_user.tenant_id
                    })
                else:
                    # 插入新记录
                    db.execute(text("""
                        INSERT INTO role_permissions (tenant_id, role_id, permission_id, created_at, updated_at)
                        VALUES (:tenant_id, :role_id, :permission_id, NOW(), NOW())
                    """), {
                        "tenant_id": current_user.tenant_id,
                        "role_id": role_id,
                        "permission_id": perm_id
                    })
        
        db.commit()
        return {"success": True, "message": "权限更新成功"}
    except Exception as e:
        db.rollback()
        print(f"更新角色权限错误: {str(e)}")
        raise HTTPException(status_code=500, detail=f"更新失败: {str(e)}")


# ============== 用户角色管理 ==============

@router.get("/roles/{role_id}/users")
async def get_role_users(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取角色下的用户（主要使用 role_id 字段）"""
    # 先获取角色信息
    role_info = db.execute(text("""
        SELECT id, code FROM roles WHERE id = :role_id AND tenant_id = :tenant_id AND deleted_at IS NULL
    """), {"role_id": role_id, "tenant_id": current_user.tenant_id}).fetchone()
    
    if not role_info:
        raise HTTPException(status_code=404, detail="角色不存在")
    
    # 只匹配 role_id 字段
    users = db.execute(text("""
        SELECT u.id, u.username, u.nickname, u.email, u.status
        FROM users u
        WHERE u.tenant_id = :tenant_id
        AND u.role_id = :role_id
        AND u.deleted_at IS NULL
    """), {"tenant_id": current_user.tenant_id, "role_id": role_info[0]}).fetchall()
    
    user_list = []
    for user in users:
        user_list.append({
            "id": user[0],
            "username": user[1],
            "nickname": user[2],
            "email": user[3],
            "status": user[4]
        })
    
    return {"success": True, "data": user_list}


class RoleUsersUpdate(BaseModel):
    user_ids: List[int]


def clean_string(s: str) -> str:
    """清理字符串中的不可见字符"""
    if not s:
        return s
    # 移除非打印字符和零宽字符
    return ''.join(c for c in s if c.isprintable() or c in ('\t', '\n', '\r'))

def clean_string_strict(s: str) -> str:
    """更严格的清理，只保留字母、数字、下划线、连字符"""
    if not s:
        return s
    return ''.join(c for c in s if c.isalnum() or c in ('_', '-'))

@router.put("/roles/{role_id}/users")
async def update_role_users(
    role_id: int,
    data: RoleUsersUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """更新角色用户（主要使用 role_id 字段，role 字段可选）"""
    # 检查角色是否存在
    role_info = db.execute(text("""
        SELECT id, code FROM roles WHERE id = :id AND tenant_id = :tenant_id AND deleted_at IS NULL
    """), {"id": role_id, "tenant_id": current_user.tenant_id}).fetchone()
    
    if not role_info:
        raise HTTPException(status_code=404, detail="角色不存在")
    
    # 清理角色编码，严格模式确保只有安全字符
    role_id_db = role_info[0]
    role_code_db = clean_string_strict(clean_string(role_info[1]))
    
    try:
        # 1. 先把所有之前属于这个角色的用户的 role_id 清空（主要依赖 role_id）
        db.execute(text("""
            UPDATE users SET role_id = NULL
            WHERE tenant_id = :tenant_id AND role_id = :role_id
        """), {"tenant_id": current_user.tenant_id, "role_id": role_id_db})
        
        # 2. 把新选择的用户的 role_id 设置为当前角色
        if data.user_ids:
            for user_id in data.user_ids:
                # 检查用户是否存在
                user = db.execute(text("""
                    SELECT id FROM users WHERE id = :id AND tenant_id = :tenant_id AND deleted_at IS NULL
                """), {"id": user_id, "tenant_id": current_user.tenant_id}).fetchone()
                
                if user:
                    # 只更新 role_id，避免 role 字段的问题
                    db.execute(text("""
                        UPDATE users SET role_id = :role_id 
                        WHERE id = :user_id AND tenant_id = :tenant_id
                    """), {
                        "role_id": role_id_db, 
                        "user_id": user_id,
                        "tenant_id": current_user.tenant_id
                    })
        
        db.commit()
        return {"success": True, "message": "用户更新成功"}
    except Exception as e:
        db.rollback()
        print(f"更新角色用户错误: {str(e)}")
        raise HTTPException(status_code=500, detail=f"更新失败: {str(e)}")


@router.get("/users")
async def get_all_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取所有用户（用于分配角色）"""
    users = db.execute(text("""
        SELECT id, username, nickname, email, status, role_id
        FROM users
        WHERE tenant_id = :tenant_id AND deleted_at IS NULL
        ORDER BY id ASC
    """), {"tenant_id": current_user.tenant_id}).fetchall()
    
    user_list = []
    for user in users:
        # 获取用户角色（通过 role_id）
        role = None
        if user[5] and user[5] > 0:  # user[5] 是 role_id
            role_result = db.execute(text("""
                SELECT r.id, r.name
                FROM roles r
                WHERE r.id = :role_id AND r.tenant_id = :tenant_id AND r.deleted_at IS NULL
            """), {"role_id": user[5], "tenant_id": current_user.tenant_id}).fetchone()
            if role_result:
                role = {"id": role_result[0], "name": role_result[1]}
        
        user_list.append({
            "id": user[0],
            "username": user[1],
            "nickname": user[2],
            "email": user[3],
            "status": user[4],
            "roles": [role] if role else []
        })
    
    return {"success": True, "data": user_list}


# ============== 当前用户权限 ==============

@router.get("/my-permissions")
async def get_my_permissions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取当前用户的权限代码列表"""
    permission_codes = await get_user_permission_codes(current_user, db)
    return {"success": True, "data": permission_codes}


# ============== 初始化默认权限 ==============

@router.post("/init-default-permissions")
async def init_default_permissions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    """初始化默认权限（仅初始化权限列表，不创建角色）"""
    tenant_id = current_user.tenant_id
    
    # 检查是否已初始化
    existing = db.execute(text("""
        SELECT COUNT(*) FROM permissions WHERE tenant_id = :tenant_id AND deleted_at IS NULL
    """), {"tenant_id": tenant_id}).scalar() or 0
    
    if existing > 0:
        return {"success": True, "message": "已初始化过，无需重复操作"}
    
    # 默认权限列表 - 覆盖所有系统模块
    default_permissions = [
        # 产品管理
        {"name": "查看产品", "code": "product:view", "type": "function", "module": "产品管理", "sort_order": 0},
        {"name": "新增产品", "code": "product:create", "type": "function", "module": "产品管理", "sort_order": 1},
        {"name": "编辑产品", "code": "product:edit", "type": "function", "module": "产品管理", "sort_order": 2},
        {"name": "删除产品", "code": "product:delete", "type": "function", "module": "产品管理", "sort_order": 3},
        {"name": "导入产品", "code": "product:import", "type": "function", "module": "产品管理", "sort_order": 4},
        {"name": "导出产品", "code": "product:export", "type": "function", "module": "产品管理", "sort_order": 5},
        # 选品管理
        {"name": "查看选品", "code": "product_selection:view", "type": "function", "module": "选品管理", "sort_order": 5},
        {"name": "新增选品", "code": "product_selection:create", "type": "function", "module": "选品管理", "sort_order": 6},
        {"name": "编辑选品", "code": "product_selection:edit", "type": "function", "module": "选品管理", "sort_order": 7},
        {"name": "删除选品", "code": "product_selection:delete", "type": "function", "module": "选品管理", "sort_order": 8},
        {"name": "审批选品", "code": "product_selection:approve", "type": "function", "module": "选品管理", "sort_order": 9},
        {"name": "申请选品", "code": "product_selection:submit", "type": "function", "module": "选品管理", "sort_order": 10},
        {"name": "选品利润设置", "code": "product_selection:settings", "type": "function", "module": "选品管理", "sort_order": 11},
        # 底表管理
        {"name": "查看底表", "code": "base_table:view", "type": "function", "module": "底表管理", "sort_order": 5},
        # 平台商品管理
        {"name": "查看平台商品", "code": "platform:view", "type": "function", "module": "产品管理", "sort_order": 6},
        {"name": "新增平台商品", "code": "platform:create", "type": "function", "module": "产品管理", "sort_order": 7},
        {"name": "编辑平台商品", "code": "platform:edit", "type": "function", "module": "产品管理", "sort_order": 8},
        {"name": "删除平台商品", "code": "platform:delete", "type": "function", "module": "产品管理", "sort_order": 9},
        # 入库管理
        {"name": "查看入库", "code": "inbound:view", "type": "function", "module": "入库管理", "sort_order": 10},
        {"name": "新增入库", "code": "inbound:create", "type": "function", "module": "入库管理", "sort_order": 11},
        {"name": "编辑入库", "code": "inbound:edit", "type": "function", "module": "入库管理", "sort_order": 12},
        {"name": "审批入库", "code": "inbound:confirm", "type": "function", "module": "入库管理", "sort_order": 13},
        {"name": "删除入库", "code": "inbound:delete", "type": "function", "module": "入库管理", "sort_order": 14},
        {"name": "导入入库", "code": "inbound:import", "type": "function", "module": "入库管理", "sort_order": 15},
        {"name": "导出入库", "code": "inbound:export", "type": "function", "module": "入库管理", "sort_order": 16},
        # 出库管理
        {"name": "查看出库", "code": "outbound:view", "type": "function", "module": "出库管理", "sort_order": 17},
        {"name": "新增出库", "code": "outbound:create", "type": "function", "module": "出库管理", "sort_order": 18},
        {"name": "编辑出库", "code": "outbound:edit", "type": "function", "module": "出库管理", "sort_order": 19},
        {"name": "审批出库", "code": "outbound:confirm", "type": "function", "module": "出库管理", "sort_order": 20},
        {"name": "删除出库", "code": "outbound:delete", "type": "function", "module": "出库管理", "sort_order": 21},
        {"name": "导入出库", "code": "outbound:import", "type": "function", "module": "出库管理", "sort_order": 22},
        {"name": "导出出库", "code": "outbound:export", "type": "function", "module": "出库管理", "sort_order": 23},
        # 采购管理
        {"name": "查看采购", "code": "purchase:view", "type": "function", "module": "采购管理", "sort_order": 24},
        {"name": "新增采购", "code": "purchase:create", "type": "function", "module": "采购管理", "sort_order": 25},
        {"name": "编辑采购", "code": "purchase:edit", "type": "function", "module": "采购管理", "sort_order": 26},
        {"name": "审批采购", "code": "purchase:confirm", "type": "function", "module": "采购管理", "sort_order": 27},
        {"name": "删除采购", "code": "purchase:delete", "type": "function", "module": "采购管理", "sort_order": 28},
        {"name": "导入采购", "code": "purchase:import", "type": "function", "module": "采购管理", "sort_order": 29},
        {"name": "导出采购", "code": "purchase:export", "type": "function", "module": "采购管理", "sort_order": 30},
        # 库存管理
        # {"name": "查看库存", "code": "inventory:view", "type": "function", "module": "库存管理", "sort_order": 31},
        # {"name": "调整库存", "code": "inventory:adjust", "type": "function", "module": "库存管理", "sort_order": 32},
        # # 店铺管理
        {"name": "查看店铺", "code": "store:view", "type": "function", "module": "店铺管理", "sort_order": 33},
        {"name": "新增店铺", "code": "store:create", "type": "function", "module": "店铺管理", "sort_order": 34},
        {"name": "编辑店铺", "code": "store:edit", "type": "function", "module": "店铺管理", "sort_order": 35},
        {"name": "删除店铺", "code": "store:delete", "type": "function", "module": "店铺管理", "sort_order": 36},
        # 组织管理
        {"name": "查看组织", "code": "org:view", "type": "function", "module": "组织管理", "sort_order": 37},
        {"name": "编辑组织", "code": "org:edit", "type": "function", "module": "组织管理", "sort_order": 38},
        # 权限管理
        {"name": "查看权限", "code": "permission:view", "type": "function", "module": "权限管理", "sort_order": 39},
        {"name": "编辑权限", "code": "permission:edit", "type": "function", "module": "权限管理", "sort_order": 40},
        # 系统管理
        {"name": "查看日志", "code": "log:view", "type": "function", "module": "系统管理", "sort_order": 41},
        {"name": "导出日志", "code": "log:export", "type": "function", "module": "系统管理", "sort_order": 42},
        # AI聊天助手
        {"name": "使用AI聊天助手", "code": "chat:use", "type": "function", "module": "AI聊天助手", "sort_order": 43},
        # 库存机器人
        {"name": "查看库存数据", "code": "robot:inventory:view", "type": "function", "module": "库存机器人", "sort_order": 44},
        {"name": "业务设置", "code": "robot:inventory:settings", "type": "function", "module": "库存机器人", "sort_order": 45},
        # 差评机器人
        {"name": "查看差评", "code": "robot:review:view", "type": "function", "module": "差评机器人", "sort_order": 46},
        {"name": "AI分析差评", "code": "robot:review:analyze", "type": "function", "module": "差评机器人", "sort_order": 47},
        {"name": "管理差评状态", "code": "robot:review:manage", "type": "function", "module": "差评机器人", "sort_order": 48},
        {"name": "差评推送配置", "code": "robot:review:push_config", "type": "function", "module": "差评机器人", "sort_order": 49},
        # 邮件机器人
        {"name": "查看邮件", "code": "robot:email:view", "type": "function", "module": "邮件机器人", "sort_order": 57},
        {"name": "AI回复邮件", "code": "robot:email:reply", "type": "function", "module": "邮件机器人", "sort_order": 58},
        {"name": "管理邮件状态", "code": "robot:email:manage", "type": "function", "module": "邮件机器人", "sort_order": 59},
        # KPI卡片权限 - 控制首页各卡片是否显示
        {"name": "差评机器人KPI卡片", "code": "robot:review:kpi", "type": "function", "module": "差评机器人", "sort_order": 48},
        {"name": "库存机器人KPI卡片", "code": "robot:inventory:kpi", "type": "function", "module": "库存机器人", "sort_order": 49},
        {"name": "AI聊天助手KPI卡片", "code": "chat:kpi", "type": "function", "module": "AI聊天助手", "sort_order": 50},
        {"name": "邮件机器人KPI卡片", "code": "robot:email:kpi", "type": "function", "module": "邮件机器人", "sort_order": 51},
        # 页面优化机器人
        {"name": "查看页面优化", "code": "robot:rating:view", "type": "function", "module": "页面优化机器人", "sort_order": 70},
        {"name": "刷新库存", "code": "robot:rating:import", "type": "function", "module": "页面优化机器人", "sort_order": 71},
        {"name": "开广告", "code": "robot:rating:ad", "type": "function", "module": "页面优化机器人", "sort_order": 72},
        # 媒体创作中心
        {"name": "AI视频", "code": "ai_video:use", "type": "function", "module": "媒体创作中心", "sort_order": 71},
        {"name": "审批视频绑定", "code": "ai_video:approve", "type": "function", "module": "媒体创作中心", "sort_order": 72},
        {"name": "AI生图", "code": "ai_image:use", "type": "function", "module": "媒体创作中心", "sort_order": 73},
        {"name": "超期采购单KPI卡片", "code": "purchase:overdue_kpi", "type": "function", "module": "采购管理", "sort_order": 52},
        {"name": "采购单状态KPI卡片", "code": "purchase:status_kpi", "type": "function", "module": "采购管理", "sort_order": 53},
        {"name": "入库差异KPI卡片", "code": "inbound:diff_kpi", "type": "function", "module": "入库管理", "sort_order": 54},
        # 挪货管理
        {"name": "查看挪货", "code": "stock_transfer:view", "type": "function", "module": "挪货管理", "sort_order": 55},
        {"name": "新增挪货", "code": "stock_transfer:create", "type": "function", "module": "挪货管理", "sort_order": 56},
        {"name": "编辑挪货", "code": "stock_transfer:edit", "type": "function", "module": "挪货管理", "sort_order": 57},
        {"name": "审批挪货", "code": "stock_transfer:confirm", "type": "function", "module": "挪货管理", "sort_order": 58},
        {"name": "删除挪货", "code": "stock_transfer:delete", "type": "function", "module": "挪货管理", "sort_order": 59},
        # 仓库管理
        {"name": "查看仓库", "code": "warehouse:view", "type": "function", "module": "仓库管理", "sort_order": 60},
        {"name": "新增仓库", "code": "warehouse:create", "type": "function", "module": "仓库管理", "sort_order": 61},
        {"name": "编辑仓库", "code": "warehouse:edit", "type": "function", "module": "仓库管理", "sort_order": 62},
        {"name": "删除仓库", "code": "warehouse:delete", "type": "function", "module": "仓库管理", "sort_order": 63},
        # 补货管理
        {"name": "查看补货", "code": "replenishment:view", "type": "function", "module": "补货管理", "sort_order": 64},
        {"name": "新增补货", "code": "replenishment:create", "type": "function", "module": "补货管理", "sort_order": 65},
        {"name": "编辑补货", "code": "replenishment:edit", "type": "function", "module": "补货管理", "sort_order": 66},
        {"name": "删除补货", "code": "replenishment:delete", "type": "function", "module": "补货管理", "sort_order": 67},
        {"name": "审批补货", "code": "replenishment:approve", "type": "function", "module": "补货管理", "sort_order": 68},
        {"name": "转采购单", "code": "replenishment:convert", "type": "function", "module": "补货管理", "sort_order": 69},
        # 发货管理
        {"name": "查看发货", "code": "shipment:view", "type": "function", "module": "发货管理", "sort_order": 70},
        {"name": "新增发货", "code": "shipment:create", "type": "function", "module": "发货管理", "sort_order": 71},
        {"name": "编辑发货", "code": "shipment:edit", "type": "function", "module": "发货管理", "sort_order": 72},
        {"name": "确认发货", "code": "shipment:confirm", "type": "function", "module": "发货管理", "sort_order": 73},
        {"name": "删除发货", "code": "shipment:delete", "type": "function", "module": "发货管理", "sort_order": 74},
        {"name": "发货管理KPI卡片", "code": "shipment:kpi", "type": "function", "module": "发货管理", "sort_order": 75},
        # 供应商管理
        {"name": "查看供应商", "code": "supplier:view", "type": "function", "module": "供应商管理", "sort_order": 76},
        {"name": "新增供应商", "code": "supplier:create", "type": "function", "module": "供应商管理", "sort_order": 77},
        {"name": "编辑供应商", "code": "supplier:edit", "type": "function", "module": "供应商管理", "sort_order": 78},
        {"name": "删除供应商", "code": "supplier:delete", "type": "function", "module": "供应商管理", "sort_order": 79},
        # 开发机器人 KPI卡片
        {"name": "新品采购KPI卡片", "code": "development:purchase_kpi", "type": "function", "module": "开发机器人", "sort_order": 80},
        {"name": "新品仓库KPI卡片", "code": "development:warehouse_kpi", "type": "function", "module": "开发机器人", "sort_order": 81},
        {"name": "新品美工KPI卡片", "code": "development:designer_kpi", "type": "function", "module": "开发机器人", "sort_order": 82},
        {"name": "新品审核KPI卡片", "code": "development:review_kpi", "type": "function", "module": "开发机器人", "sort_order": 83},
        # 开发机器人 功能权限
        {"name": "查看开发机器人", "code": "development:view", "type": "function", "module": "开发机器人", "sort_order": 84},
        {"name": "新增新品", "code": "development:create", "type": "function", "module": "开发机器人", "sort_order": 85},
        {"name": "编辑新品", "code": "development:edit", "type": "function", "module": "开发机器人", "sort_order": 86},
        {"name": "删除新品", "code": "development:delete", "type": "function", "module": "开发机器人", "sort_order": 87},
        {"name": "导入新品", "code": "development:import", "type": "function", "module": "开发机器人", "sort_order": 88},
        {"name": "复制新品", "code": "development:copy", "type": "function", "module": "开发机器人", "sort_order": 89},
        {"name": "采购填写", "code": "development:purchase_fill", "type": "function", "module": "开发机器人", "sort_order": 90},
        {"name": "生成采购单", "code": "development:create_order", "type": "function", "module": "开发机器人", "sort_order": 91},
        {"name": "上传六面图", "code": "development:warehouse", "type": "function", "module": "开发机器人", "sort_order": 92},
        {"name": "上传产品图", "code": "development:designer_upload", "type": "function", "module": "开发机器人", "sort_order": 93},
        {"name": "审核产品图", "code": "development:review", "type": "function", "module": "开发机器人", "sort_order": 94},
        {"name": "回传飞书", "code": "development:push_feishu", "type": "function", "module": "开发机器人", "sort_order": 95},
    ]

    for perm in default_permissions:
        db.execute(text("""
            INSERT INTO permissions (tenant_id, name, code, type, module, sort_order, created_at, updated_at)
            VALUES (:tenant_id, :name, :code, :type, :module, :sort_order, NOW(), NOW())
        """), {
            "tenant_id": tenant_id,
            "name": perm["name"],
            "code": perm["code"],
            "type": perm["type"],
            "module": perm["module"],
            "sort_order": perm["sort_order"]
        })
    
    db.commit()
    
    return {"success": True, "message": f"初始化成功，共创建 {len(default_permissions)} 个权限"}


@router.post("/add-missing-permissions")
async def add_missing_permissions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """为已初始化的租户补充缺失的新权限"""
    tenant_id = current_user.tenant_id
    
    # 新权限列表（需要补充的）
    new_permissions = [
        # 选品管理
        {"name": "查看选品", "code": "product_selection:view", "type": "function", "module": "选品管理", "sort_order": 5},
        {"name": "新增选品", "code": "product_selection:create", "type": "function", "module": "选品管理", "sort_order": 6},
        {"name": "编辑选品", "code": "product_selection:edit", "type": "function", "module": "选品管理", "sort_order": 7},
        {"name": "删除选品", "code": "product_selection:delete", "type": "function", "module": "选品管理", "sort_order": 8},
        {"name": "审批选品", "code": "product_selection:approve", "type": "function", "module": "选品管理", "sort_order": 9},
        {"name": "申请选品", "code": "product_selection:submit", "type": "function", "module": "选品管理", "sort_order": 10},
        {"name": "选品利润设置", "code": "product_selection:settings", "type": "function", "module": "选品管理", "sort_order": 11},
        # 底表管理
        {"name": "查看底表", "code": "base_table:view", "type": "function", "module": "底表管理", "sort_order": 5},
        # 平台商品管理
        {"name": "查看平台商品", "code": "platform:view", "type": "function", "module": "产品管理", "sort_order": 6},
        {"name": "新增平台商品", "code": "platform:create", "type": "function", "module": "产品管理", "sort_order": 7},
        {"name": "编辑平台商品", "code": "platform:edit", "type": "function", "module": "产品管理", "sort_order": 8},
        {"name": "删除平台商品", "code": "platform:delete", "type": "function", "module": "产品管理", "sort_order": 9},
        # 采购审批
        {"name": "审批采购", "code": "purchase:confirm", "type": "function", "module": "采购管理", "sort_order": 27},
        # AI聊天助手
        {"name": "使用AI聊天助手", "code": "chat:use", "type": "function", "module": "AI聊天助手", "sort_order": 43},
        # 库存机器人
        {"name": "查看库存数据", "code": "robot:inventory:view", "type": "function", "module": "库存机器人", "sort_order": 44},
        {"name": "业务设置", "code": "robot:inventory:settings", "type": "function", "module": "库存机器人", "sort_order": 45},
        # 差评机器人
        {"name": "查看差评", "code": "robot:review:view", "type": "function", "module": "差评机器人", "sort_order": 46},
        {"name": "AI分析差评", "code": "robot:review:analyze", "type": "function", "module": "差评机器人", "sort_order": 47},
        {"name": "管理差评状态", "code": "robot:review:manage", "type": "function", "module": "差评机器人", "sort_order": 48},
        {"name": "差评推送配置", "code": "robot:review:push_config", "type": "function", "module": "差评机器人", "sort_order": 49},
        # 邮件机器人
        {"name": "查看邮件", "code": "robot:email:view", "type": "function", "module": "邮件机器人", "sort_order": 57},
        {"name": "AI回复邮件", "code": "robot:email:reply", "type": "function", "module": "邮件机器人", "sort_order": 58},
        {"name": "管理邮件状态", "code": "robot:email:manage", "type": "function", "module": "邮件机器人", "sort_order": 59},
        # KPI卡片权限 - 控制首页各卡片是否显示
        {"name": "差评机器人KPI卡片", "code": "robot:review:kpi", "type": "function", "module": "差评机器人", "sort_order": 48},
        {"name": "库存机器人KPI卡片", "code": "robot:inventory:kpi", "type": "function", "module": "库存机器人", "sort_order": 49},
        {"name": "AI聊天助手KPI卡片", "code": "chat:kpi", "type": "function", "module": "AI聊天助手", "sort_order": 50},
        {"name": "邮件机器人KPI卡片", "code": "robot:email:kpi", "type": "function", "module": "邮件机器人", "sort_order": 51},
        # 页面优化机器人
        {"name": "查看页面优化", "code": "robot:rating:view", "type": "function", "module": "页面优化机器人", "sort_order": 70},
        {"name": "刷新库存", "code": "robot:rating:import", "type": "function", "module": "页面优化机器人", "sort_order": 71},
        {"name": "开广告", "code": "robot:rating:ad", "type": "function", "module": "页面优化机器人", "sort_order": 72},
        # 媒体创作中心
        {"name": "AI视频", "code": "ai_video:use", "type": "function", "module": "媒体创作中心", "sort_order": 71},
        {"name": "审批视频绑定", "code": "ai_video:approve", "type": "function", "module": "媒体创作中心", "sort_order": 72},
        {"name": "AI生图", "code": "ai_image:use", "type": "function", "module": "媒体创作中心", "sort_order": 73},
        {"name": "超期采购单KPI卡片", "code": "purchase:overdue_kpi", "type": "function", "module": "采购管理", "sort_order": 52},
        {"name": "采购单状态KPI卡片", "code": "purchase:status_kpi", "type": "function", "module": "采购管理", "sort_order": 53},
        {"name": "入库差异KPI卡片", "code": "inbound:diff_kpi", "type": "function", "module": "入库管理", "sort_order": 54},
        # 挪货管理
        {"name": "查看挪货", "code": "stock_transfer:view", "type": "function", "module": "挪货管理", "sort_order": 55},
        {"name": "新增挪货", "code": "stock_transfer:create", "type": "function", "module": "挪货管理", "sort_order": 56},
        {"name": "编辑挪货", "code": "stock_transfer:edit", "type": "function", "module": "挪货管理", "sort_order": 57},
        {"name": "审批挪货", "code": "stock_transfer:confirm", "type": "function", "module": "挪货管理", "sort_order": 58},
        {"name": "删除挪货", "code": "stock_transfer:delete", "type": "function", "module": "挪货管理", "sort_order": 59},
        # 仓库管理
        {"name": "查看仓库", "code": "warehouse:view", "type": "function", "module": "仓库管理", "sort_order": 60},
        {"name": "新增仓库", "code": "warehouse:create", "type": "function", "module": "仓库管理", "sort_order": 61},
        {"name": "编辑仓库", "code": "warehouse:edit", "type": "function", "module": "仓库管理", "sort_order": 62},
        {"name": "删除仓库", "code": "warehouse:delete", "type": "function", "module": "仓库管理", "sort_order": 63},
        # 补货管理
        {"name": "查看补货", "code": "replenishment:view", "type": "function", "module": "补货管理", "sort_order": 64},
        {"name": "新增补货", "code": "replenishment:create", "type": "function", "module": "补货管理", "sort_order": 65},
        {"name": "编辑补货", "code": "replenishment:edit", "type": "function", "module": "补货管理", "sort_order": 66},
        {"name": "删除补货", "code": "replenishment:delete", "type": "function", "module": "补货管理", "sort_order": 67},
        {"name": "审批补货", "code": "replenishment:approve", "type": "function", "module": "补货管理", "sort_order": 68},
        {"name": "转采购单", "code": "replenishment:convert", "type": "function", "module": "补货管理", "sort_order": 69},
        # 发货管理
        {"name": "查看发货", "code": "shipment:view", "type": "function", "module": "发货管理", "sort_order": 70},
        {"name": "新增发货", "code": "shipment:create", "type": "function", "module": "发货管理", "sort_order": 71},
        {"name": "编辑发货", "code": "shipment:edit", "type": "function", "module": "发货管理", "sort_order": 72},
        {"name": "确认发货", "code": "shipment:confirm", "type": "function", "module": "发货管理", "sort_order": 73},
        {"name": "删除发货", "code": "shipment:delete", "type": "function", "module": "发货管理", "sort_order": 74},
        {"name": "发货管理KPI卡片", "code": "shipment:kpi", "type": "function", "module": "发货管理", "sort_order": 75},
        # 供应商管理
        {"name": "查看供应商", "code": "supplier:view", "type": "function", "module": "供应商管理", "sort_order": 76},
        {"name": "新增供应商", "code": "supplier:create", "type": "function", "module": "供应商管理", "sort_order": 77},
        {"name": "编辑供应商", "code": "supplier:edit", "type": "function", "module": "供应商管理", "sort_order": 78},
        {"name": "删除供应商", "code": "supplier:delete", "type": "function", "module": "供应商管理", "sort_order": 79},
        # 开发机器人 KPI卡片
        {"name": "新品采购KPI卡片", "code": "development:purchase_kpi", "type": "function", "module": "开发机器人", "sort_order": 80},
        {"name": "新品仓库KPI卡片", "code": "development:warehouse_kpi", "type": "function", "module": "开发机器人", "sort_order": 81},
        {"name": "新品美工KPI卡片", "code": "development:designer_kpi", "type": "function", "module": "开发机器人", "sort_order": 82},
        {"name": "新品审核KPI卡片", "code": "development:review_kpi", "type": "function", "module": "开发机器人", "sort_order": 83},
        # 开发机器人 功能权限
        {"name": "查看开发机器人", "code": "development:view", "type": "function", "module": "开发机器人", "sort_order": 84},
        {"name": "新增新品", "code": "development:create", "type": "function", "module": "开发机器人", "sort_order": 85},
        {"name": "编辑新品", "code": "development:edit", "type": "function", "module": "开发机器人", "sort_order": 86},
        {"name": "删除新品", "code": "development:delete", "type": "function", "module": "开发机器人", "sort_order": 87},
        {"name": "导入新品", "code": "development:import", "type": "function", "module": "开发机器人", "sort_order": 88},
        {"name": "复制新品", "code": "development:copy", "type": "function", "module": "开发机器人", "sort_order": 89},
        {"name": "采购填写", "code": "development:purchase_fill", "type": "function", "module": "开发机器人", "sort_order": 90},
        {"name": "生成采购单", "code": "development:create_order", "type": "function", "module": "开发机器人", "sort_order": 91},
        {"name": "上传六面图", "code": "development:warehouse", "type": "function", "module": "开发机器人", "sort_order": 92},
        {"name": "上传产品图", "code": "development:designer_upload", "type": "function", "module": "开发机器人", "sort_order": 93},
        {"name": "审核产品图", "code": "development:review", "type": "function", "module": "开发机器人", "sort_order": 94},
        {"name": "回传飞书", "code": "development:push_feishu", "type": "function", "module": "开发机器人", "sort_order": 95},
    ]

    # 先清理无用的旧权限码
    removed_codes = ["dashboard:view", "review:reply", "robot:inventory", "robot:review", "replenishment:confirm", "replenishment:import"]
    removed_count = 0
    for code in removed_codes:
        result = db.execute(text("""
            UPDATE permissions SET deleted_at = NOW() 
            WHERE tenant_id = :tid AND code = :code AND deleted_at IS NULL
        """), {"tid": tenant_id, "code": code})
        removed_count += result.rowcount

    added_count = 0
    added_permission_ids = []
    
    for perm in new_permissions:
        existing = db.execute(text("""
            SELECT id FROM permissions 
            WHERE tenant_id = :tenant_id AND code = :code AND deleted_at IS NULL
        """), {"tenant_id": tenant_id, "code": perm["code"]}).fetchone()
        
        if not existing:
            result = db.execute(text("""
                INSERT INTO permissions (tenant_id, name, code, type, module, sort_order, created_at, updated_at)
                VALUES (:tenant_id, :name, :code, :type, :module, :sort_order, NOW(), NOW())
            """), {
                "tenant_id": tenant_id,
                "name": perm["name"],
                "code": perm["code"],
                "type": perm["type"],
                "module": perm["module"],
                "sort_order": perm["sort_order"]
            })
            added_count += 1
            # 获取新插入的权限ID
            new_perm = db.execute(text("""
                SELECT id FROM permissions 
                WHERE tenant_id = :tenant_id AND code = :code AND deleted_at IS NULL
            """), {"tenant_id": tenant_id, "code": perm["code"]}).fetchone()
            if new_perm:
                added_permission_ids.append(new_perm[0])
    
    # 给admin角色分配新增的权限
    if added_permission_ids:
        admin_role = db.execute(text("""
            SELECT id FROM roles 
            WHERE tenant_id = :tenant_id AND code = 'admin' AND deleted_at IS NULL
        """), {"tenant_id": tenant_id}).fetchone()
        if admin_role:
            for perm_id in added_permission_ids:
                # 检查是否已分配
                existing_rp = db.execute(text("""
                    SELECT id FROM role_permissions 
                    WHERE tenant_id = :tenant_id AND role_id = :role_id AND permission_id = :perm_id
                """), {"tenant_id": tenant_id, "role_id": admin_role[0], "perm_id": perm_id}).fetchone()
                if not existing_rp:
                    db.execute(text("""
                        INSERT INTO role_permissions (tenant_id, role_id, permission_id, created_at, updated_at)
                        VALUES (:tenant_id, :role_id, :perm_id, NOW(), NOW())
                    """), {"tenant_id": tenant_id, "role_id": admin_role[0], "perm_id": perm_id})
    
    # 迁移旧「AI创作中心」权限到「媒体创作中心-AI视频」（保留角色绑定关系）
    legacy_perm = db.execute(text("""
        SELECT id FROM permissions
        WHERE tenant_id = :tid AND code = 'ai_creation:use' AND deleted_at IS NULL
    """), {"tid": tenant_id}).fetchone()
    new_perm = db.execute(text("""
        SELECT id FROM permissions
        WHERE tenant_id = :tid AND code = 'ai_video:use' AND deleted_at IS NULL
    """), {"tid": tenant_id}).fetchone()
    if legacy_perm and new_perm and legacy_perm[0] != new_perm[0]:
        # 新旧并存：把旧权限的角色绑定迁移到新权限后软删旧权限
        db.execute(text("""
            UPDATE role_permissions SET permission_id = :new_pid, updated_at = NOW()
            WHERE permission_id = :old_pid AND deleted_at IS NULL
              AND id NOT IN (
                SELECT id FROM (SELECT id FROM role_permissions
                                WHERE permission_id = :new_pid AND deleted_at IS NULL) t
              )
        """), {"new_pid": new_perm[0], "old_pid": legacy_perm[0]})
        db.execute(text("""
            UPDATE permissions SET deleted_at = NOW(), updated_at = NOW()
            WHERE id = :pid
        """), {"pid": legacy_perm[0]})
    elif legacy_perm and not new_perm:
        # 仅存在旧权限：原地改码改名（角色绑定不受影响）
        db.execute(text("""
            UPDATE permissions
            SET code = 'ai_video:use', name = 'AI视频', module = '媒体创作中心', updated_at = NOW()
            WHERE id = :pid
        """), {"pid": legacy_perm[0]})

    # 规范化已有权限的模块和名称（防止之前的数据错乱）
    fix_map = [
        {"code": "inbound:confirm", "name": "审批入库", "module": "入库管理"},
        {"code": "outbound:confirm", "name": "审批出库", "module": "出库管理"},
        {"code": "chat:use", "name": "使用AI聊天助手", "module": "AI聊天助手"},
        {"code": "robot:inventory:view", "name": "查看库存数据", "module": "库存机器人"},
        {"code": "robot:review:view", "name": "查看差评", "module": "差评机器人"},
        {"code": "robot:review:analyze", "name": "AI分析差评", "module": "差评机器人"},
        {"code": "robot:review:manage", "name": "管理差评状态", "module": "差评机器人"},
        {"code": "robot:rating:view", "name": "查看页面优化", "module": "页面优化机器人"},
        {"code": "robot:rating:import", "name": "刷新库存", "module": "页面优化机器人"},
        {"code": "robot:rating:ad", "name": "开广告", "module": "页面优化机器人"},
    ]
    fixed_count = 0
    for fm in fix_map:
        result = db.execute(text("""
            UPDATE permissions SET name = :name, module = :module
            WHERE tenant_id = :tid AND code = :code AND deleted_at IS NULL AND (module != :module OR name != :name)
        """), {"tid": tenant_id, "code": fm["code"], "name": fm["name"], "module": fm["module"]})
        fixed_count += result.rowcount
    
    db.commit()
    
    return {"success": True, "message": f"补充权限成功，添加 {added_count} 个，清理无用权限 {removed_count} 个"}


@router.post("/fix-role-codes")
async def fix_role_codes(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """修复角色编码中的不可见字符"""
    tenant_id = current_user.tenant_id
    
    # 获取所有角色
    roles = db.execute(text("""
        SELECT id, code FROM roles WHERE tenant_id = :tenant_id AND deleted_at IS NULL
    """), {"tenant_id": tenant_id}).fetchall()
    
    fixed_count = 0
    for role in roles:
        original_code = role[1]
        cleaned_code = clean_string(original_code)
        cleaned_code = clean_string_strict(cleaned_code)
        
        if original_code != cleaned_code:
            # 检查清理后的编码是否已存在
            existing = db.execute(text("""
                SELECT id FROM roles 
                WHERE tenant_id = :tenant_id AND code = :code AND id != :id AND deleted_at IS NULL
            """), {"tenant_id": tenant_id, "code": cleaned_code, "id": role[0]}).fetchone()
            
            if not existing:
                # 更新角色编码
                db.execute(text("""
                    UPDATE roles SET code = :code, updated_at = NOW()
                    WHERE id = :id AND tenant_id = :tenant_id
                """), {"code": cleaned_code, "id": role[0], "tenant_id": tenant_id})
                
                # 同时更新用户表中的 role 字段
                db.execute(text("""
                    UPDATE users SET role = :code
                    WHERE tenant_id = :tenant_id AND role = :old_code
                """), {"code": cleaned_code, "tenant_id": tenant_id, "old_code": original_code})
                
                fixed_count += 1
    
    db.commit()
    return {"success": True, "message": f"修复完成，共修复 {fixed_count} 个角色"}
