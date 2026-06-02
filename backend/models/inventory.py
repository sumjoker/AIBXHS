from sqlalchemy import Column, Integer, String, Enum, DateTime, ForeignKey, JSON, Boolean, Date
from sqlalchemy.orm import relationship
from models.base import BaseModel
import enum


class InventorySource(str, enum.Enum):
    MANUAL = "manual"
    API_SYNC = "api_sync"
    IMPORT = "import"


class AlertType(str, enum.Enum):
    LOW_STOCK = "low_stock"
    OUT_OF_STOCK = "out_of_stock"
    OVERSTOCK = "overstock"
    PRICE_CHANGE = "price_change"


class AlertSeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    DANGER = "danger"
    CRITICAL = "critical"


class AlertStatus(str, enum.Enum):
    NEW = "new"
    ACKNOWLEDGED = "acknowledged"
    PROCESSING = "processing"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class ActionType(str, enum.Enum):
    PRICE_ADJUST = "price_adjust"
    AD_BUDGET = "ad_budget"
    PROMOTION = "promotion"
    RESTOCK = "restock"
    OTHER = "other"


class ActionStatus(str, enum.Enum):
    PENDING = "pending"
    EXECUTING = "executing"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TriggeredBy(str, enum.Enum):
    SYSTEM_AUTO = "system_auto"
    MANUAL = "manual"
    SCHEDULE = "schedule"


class InventoryRecord(BaseModel):
    """库存记录表"""
    __tablename__ = "inventory_records"
    
    id = Column(Integer, primary_key=True, index=True, comment="记录ID")
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True, comment="租户ID")
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True, comment="商品ID")
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False, index=True, comment="店铺ID")
    warehouse_code = Column(String(50), nullable=True, comment="仓库编码")
    quantity = Column(Integer, nullable=False, comment="当前库存")
    quantity_in_transit = Column(Integer, default=0, comment="在途库存")
    quantity_available = Column(Integer, nullable=False, comment="可用库存")
    quantity_reserved = Column(Integer, default=0, comment="预留库存")
    safe_stock = Column(Integer, default=0, comment="安全库存")
    daily_sales = Column(Integer, nullable=True, comment="日均销量")
    days_remaining = Column(Integer, nullable=True, comment="可售天数")
    record_date = Column(Date, nullable=False, index=True, comment="记录日期")
    source = Column(Enum(InventorySource), default=InventorySource.API_SYNC, comment="数据来源")
    
    # 关联关系（移除 back_populates 以避免与 Product 的循环依赖）
    product = relationship("Product", foreign_keys=[product_id])


class InventoryAlert(BaseModel):
    """库存预警表"""
    __tablename__ = "inventory_alerts"
    
    id = Column(Integer, primary_key=True, index=True, comment="预警ID")
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True, comment="租户ID")
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True, comment="商品ID")
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False, index=True, comment="店铺ID")
    alert_type = Column(Enum(AlertType), nullable=False, index=True, comment="预警类型")
    severity = Column(Enum(AlertSeverity), default=AlertSeverity.WARNING, comment="严重程度")
    title = Column(String(200), nullable=False, comment="预警标题")
    description = Column(String(1000), nullable=True, comment="预警描述")
    current_stock = Column(Integer, nullable=True, comment="当前库存")
    safe_stock = Column(Integer, nullable=True, comment="安全库存")
    suggestions = Column(JSON, nullable=True, comment="AI建议")
    status = Column(Enum(AlertStatus), default=AlertStatus.NEW, index=True, comment="处理状态")
    priority = Column(Integer, default=5, comment="优先级(1-10)")
    resolved_by = Column(Integer, ForeignKey("users.id"), nullable=True, comment="处理人")
    resolved_at = Column(DateTime, nullable=True, comment="处理时间")
    resolved_note = Column(String(1000), nullable=True, comment="处理备注")
    feishu_record_id = Column(String(100), nullable=True, comment="飞书记录ID")
    
    # 关联关系（移除 back_populates）
    product = relationship("Product", foreign_keys=[product_id])
    actions = relationship("InventoryAction", back_populates="alert", cascade="all, delete-orphan")


class InventoryAction(BaseModel):
    """库存操作记录表"""
    __tablename__ = "inventory_actions"
    
    id = Column(Integer, primary_key=True, index=True, comment="操作ID")
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True, comment="租户ID")
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True, comment="商品ID")
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False, index=True, comment="店铺ID")
    alert_id = Column(Integer, ForeignKey("inventory_alerts.id"), nullable=True, index=True, comment="关联预警ID")
    action_type = Column(Enum(ActionType), nullable=False, comment="操作类型")
    action_title = Column(String(200), nullable=False, comment="操作标题")
    action_details = Column(JSON, nullable=True, comment="操作详情")
    status = Column(Enum(ActionStatus), default=ActionStatus.PENDING, index=True, comment="执行状态")
    triggered_by = Column(Enum(TriggeredBy), default=TriggeredBy.MANUAL, comment="触发方式")
    result = Column(String(1000), nullable=True, comment="执行结果")
    error_message = Column(String(1000), nullable=True, comment="错误信息")
    executed_by = Column(Integer, ForeignKey("users.id"), nullable=True, comment="执行人")
    executed_at = Column(DateTime, nullable=True, comment="执行时间")
    
    # 关联关系（移除 back_populates）
    product = relationship("Product", foreign_keys=[product_id])
    alert = relationship("InventoryAlert", back_populates="actions")
