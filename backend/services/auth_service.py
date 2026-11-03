from datetime import datetime, timedelta
from typing import Optional
from passlib.context import CryptContext
from jose import JWTError, jwt
from sqlalchemy.orm import Session
from models.user import User
from config import get_settings

settings = get_settings()

# 密码加密上下文 - 使用 pbkdf2_sha256 替代 bcrypt，避免 bcrypt 的环境问题
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """哈希密码"""
    # bcrypt 限制密码长度不能超过72字节
    password = password[:72]
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """创建访问令牌"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


def get_user(db: Session, username: str) -> Optional[User]:
    """根据用户名或邮箱获取用户"""
    user = db.query(User).filter(User.username == username).first()
    if user:
        return user
    # 如果没有找到用户，尝试通过邮箱查找
    return db.query(User).filter(User.email == username).first()


def get_user_by_identity(
    db: Session,
    user_id: Optional[int] = None,
    tenant_id: Optional[int] = None,
    username: Optional[str] = None
) -> Optional[User]:
    """?????ID???ID??????? token ???????"""
    query = db.query(User)

    if user_id is not None:
        query = query.filter(User.id == user_id)
        if tenant_id is not None:
            query = query.filter(User.tenant_id == tenant_id)
        return query.first()

    if username is not None and tenant_id is not None:
        return query.filter(User.username == username, User.tenant_id == tenant_id).first()

    if username is not None:
        return query.filter(User.username == username).first()

    return None



def authenticate_user(db: Session, username: str, password: str) -> Optional[User]:
    """认证用户"""
    user = get_user(db, username)
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def create_user(
    db: Session,
    username: str,
    email: str,
    password: str,
    nickname: Optional[str] = None,
    company_name: Optional[str] = None,
    company_code: Optional[str] = None
) -> User:
    """创建用户（支持公司选择）"""
    from models.tenant import Tenant
    from sqlalchemy import text
    import uuid, random, string

    # 标记是否是新创建的租户
    is_new_tenant = False

    if company_code:
        company_code = company_code.strip()
        # 查找已有公司
        tenant = db.query(Tenant).filter(Tenant.code == company_code, Tenant.deleted_at.is_(None)).first()
        if tenant:
            # 公司已存在，加入该公司
            is_new_tenant = False
        else:
            # 公司不存在，自动创建
            company_name = (company_name or company_code).strip()
            tenant = Tenant(
                name=company_name,
                code=company_code,
                is_personal=0
            )
            db.add(tenant)
            db.commit()
            db.refresh(tenant)
            is_new_tenant = True
    else:
        # 未填公司编号，自动创建个人公司
        tenant = Tenant(
            name=f"{username}的个人公司",
            code=f"personal-{uuid.uuid4().hex[:8]}",
            is_personal=1
        )
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
        is_new_tenant = True

    # 如果是新创建的租户，自动初始化权限和管理员角色
    if is_new_tenant:
        _init_tenant_permissions_and_admin_role(db, tenant.id)

    hashed_password = get_password_hash(password)
    user = User(
        tenant_id=tenant.id,
        username=username,
        email=email,
        password_hash=hashed_password,
        nickname=nickname or username
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # 如果是新创建的租户，将注册用户设置为管理员
    if is_new_tenant:
        # 获取管理员角色
        admin_role = db.execute(text("""
            SELECT id FROM roles 
            WHERE tenant_id = :tenant_id AND code = 'admin' AND deleted_at IS NULL
        """), {"tenant_id": tenant.id}).fetchone()
        
        if admin_role:
            # 设置用户角色为管理员
            db.execute(text("""
                UPDATE users SET role_id = :role_id WHERE id = :user_id
            """), {"role_id": admin_role[0], "user_id": user.id})
            db.commit()
            db.refresh(user)

    return user


def _init_tenant_permissions_and_admin_role(db: Session, tenant_id: int):
    """初始化租户的默认权限和管理员角色"""
    from sqlalchemy import text

    # 1. 初始化默认权限
    default_permissions = [
        # 产品管理
        {"name": "查看产品", "code": "product:view", "type": "function", "module": "产品管理", "sort_order": 0},
        {"name": "新增产品", "code": "product:create", "type": "function", "module": "产品管理", "sort_order": 1},
        {"name": "编辑产品", "code": "product:edit", "type": "function", "module": "产品管理", "sort_order": 2},
        {"name": "删除产品", "code": "product:delete", "type": "function", "module": "产品管理", "sort_order": 3},
        {"name": "导入产品", "code": "product:import", "type": "function", "module": "产品管理", "sort_order": 4},
        {"name": "导出产品", "code": "product:export", "type": "function", "module": "产品管理", "sort_order": 5},
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
        {"name": "入库差异KPI卡片", "code": "inbound:diff_kpi", "type": "function", "module": "入库管理", "sort_order": 54},
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
        {"name": "超期采购单KPI卡片", "code": "purchase:overdue_kpi", "type": "function", "module": "采购管理", "sort_order": 52},
        {"name": "采购单状态KPI卡片", "code": "purchase:status_kpi", "type": "function", "module": "采购管理", "sort_order": 53},
        # 店铺管理
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
        {"name": "AI聊天助手KPI卡片", "code": "chat:kpi", "type": "function", "module": "AI聊天助手", "sort_order": 50},
        # 库存机器人
        {"name": "查看库存数据", "code": "robot:inventory:view", "type": "function", "module": "库存机器人", "sort_order": 44},
        {"name": "业务设置", "code": "robot:inventory:settings", "type": "function", "module": "库存机器人", "sort_order": 45},
        {"name": "库存机器人KPI卡片", "code": "robot:inventory:kpi", "type": "function", "module": "库存机器人", "sort_order": 49},
        # 差评机器人
        {"name": "查看差评", "code": "robot:review:view", "type": "function", "module": "差评机器人", "sort_order": 46},
        {"name": "AI分析差评", "code": "robot:review:analyze", "type": "function", "module": "差评机器人", "sort_order": 47},
        {"name": "管理差评状态", "code": "robot:review:manage", "type": "function", "module": "差评机器人", "sort_order": 48},
        {"name": "差评推送配置", "code": "robot:review:push_config", "type": "function", "module": "差评机器人", "sort_order": 49},
        {"name": "差评机器人KPI卡片", "code": "robot:review:kpi", "type": "function", "module": "差评机器人", "sort_order": 48},
        # 邮件机器人
        {"name": "查看邮件", "code": "robot:email:view", "type": "function", "module": "邮件机器人", "sort_order": 57},
        {"name": "AI回复邮件", "code": "robot:email:reply", "type": "function", "module": "邮件机器人", "sort_order": 58},
        {"name": "管理邮件状态", "code": "robot:email:manage", "type": "function", "module": "邮件机器人", "sort_order": 59},
        {"name": "邮件机器人KPI卡片", "code": "robot:email:kpi", "type": "function", "module": "邮件机器人", "sort_order": 51},
        # 页面优化机器人
        {"name": "查看页面优化", "code": "robot:rating:view", "type": "function", "module": "页面优化机器人", "sort_order": 70},
        {"name": "刷新库存", "code": "robot:rating:import", "type": "function", "module": "页面优化机器人", "sort_order": 71},
        {"name": "开广告", "code": "robot:rating:ad", "type": "function", "module": "页面优化机器人", "sort_order": 72},
        # 媒体创作中心
        {"name": "AI视频", "code": "ai_video:use", "type": "function", "module": "媒体创作中心", "sort_order": 71},
        {"name": "审批视频绑定", "code": "ai_video:approve", "type": "function", "module": "媒体创作中心", "sort_order": 72},
        {"name": "AI生图", "code": "ai_image:use", "type": "function", "module": "媒体创作中心", "sort_order": 73},
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
        # 盘点管理
        {"name": "查看盘点", "code": "inventory_count:view", "type": "function", "module": "盘点管理", "sort_order": 76},
        {"name": "新增盘点", "code": "inventory_count:create", "type": "function", "module": "盘点管理", "sort_order": 77},
        {"name": "编辑盘点", "code": "inventory_count:edit", "type": "function", "module": "盘点管理", "sort_order": 78},
        {"name": "审批盘点", "code": "inventory_count:confirm", "type": "function", "module": "盘点管理", "sort_order": 79},
        {"name": "删除盘点", "code": "inventory_count:delete", "type": "function", "module": "盘点管理", "sort_order": 80},
    ]

    permission_ids = []
    for perm in default_permissions:
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
        permission_ids.append(result.lastrowid)

    # 2. 创建管理员角色（系统内置角色，不可删除）
    role_result = db.execute(text("""
        INSERT INTO roles (tenant_id, name, code, description, is_system, sort_order, created_at, updated_at)
        VALUES (:tenant_id, :name, :code, :description, :is_system, :sort_order, NOW(), NOW())
    """), {
        "tenant_id": tenant_id,
        "name": "管理员",
        "code": "admin",
        "description": "系统管理员，拥有所有权限",
        "is_system": True,  # 标记为系统内置角色
        "sort_order": 0
    })
    admin_role_id = role_result.lastrowid

    # 3. 给管理员角色分配所有权限
    for perm_id in permission_ids:
        db.execute(text("""
            INSERT INTO role_permissions (tenant_id, role_id, permission_id, created_at, updated_at)
            VALUES (:tenant_id, :role_id, :permission_id, NOW(), NOW())
        """), {
            "tenant_id": tenant_id,
            "role_id": admin_role_id,
            "permission_id": perm_id
        })

    db.commit()
