from sqlalchemy import create_engine, text, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool
from config import get_settings

settings = get_settings()

# 创建数据库引擎，确保使用utf8mb4
engine = create_engine(
    settings.DATABASE_URL,
    poolclass=QueuePool,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
    pool_recycle=1800,  # 30分钟
    echo=False
)

# 添加事件监听器，确保每次连接都设置正确的字符集
@event.listens_for(engine, "connect")
def connect(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("SET NAMES 'utf8mb4' COLLATE 'utf8mb4_unicode_ci'")
    cursor.close()

# 创建会话工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 创建基类
Base = declarative_base()


def get_db():
    """获取数据库会话"""
    db = SessionLocal()
    try:
        # 确保每个会话的字符集设置正确
        db.execute(text("SET NAMES 'utf8mb4' COLLATE 'utf8mb4_unicode_ci'"))
        yield db
    finally:
        db.close()


def init_db():
    """初始化数据库"""
    try:
        # 导入所有模型（确保所有模型都被注册）
        from models import base
        from models import tenant
        from models import user
        from models import store
        from models import product
        from models import inventory
        from models import review
        from models import conversation
        from models import department
        
        # 导入所有模型类
        from models.tenant import Tenant
        from models.user import User
        from models.store import Store
        from models.product import Product
        from models.inventory import InventoryRecord, InventoryAlert, InventoryAction
        from models.review import Review, ReviewAnalysis, ReviewHandling
        from models.conversation import ConversationHistory
        from models.department import Department, UserDepartment
        from models.restock import InventorySnapshot, InboundShipmentDetail, ReplenishmentDecision
        from models.local_inventory import LocalInventory
        
        # 创建所有表
        Base.metadata.create_all(bind=engine)
        print("数据库表结构创建成功")
    except Exception as e:
        print(f"数据库初始化失败: {e}")
        raise
