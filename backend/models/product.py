from sqlalchemy import Column, Integer, String, Enum, DateTime, ForeignKey, JSON, Boolean, DECIMAL
from sqlalchemy.orm import relationship
from models.base import BaseModel
import enum


class ProductStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    ARCHIVED = "archived"


class Product(BaseModel):
    """商品模型"""
    __tablename__ = "products"
    
    id = Column(Integer, primary_key=True, index=True, comment="商品ID")
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True, comment="租户ID")
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False, index=True, comment="店铺ID")
    asin = Column(String(50), nullable=False, comment="ASIN/商品编码")
    sku = Column(String(100), nullable=True, comment="SKU")
    name = Column(String(255), nullable=False, comment="商品名称")
    name_en = Column(String(255), nullable=True, comment="英文名称")
    image_url = Column(String(500), nullable=True, comment="商品图片")
    category = Column(String(100), nullable=True, index=True, comment="商品分类")
    brand = Column(String(100), nullable=True, comment="品牌")
    price = Column(DECIMAL(12, 2), nullable=True, comment="售价")
    cost_price = Column(DECIMAL(12, 2), nullable=True, comment="成本价")
    status = Column(Enum(ProductStatus), default=ProductStatus.ACTIVE, index=True, comment="状态")
    is_robot_monitored = Column(Boolean, default=True, comment="是否机器人监控")
    config = Column(JSON, nullable=True, comment="商品配置(安全库存等)")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True, comment="创建人")
    
    # 关联关系
    tenant = relationship("Tenant", back_populates="products")
    # Store 可能没有 products relationship，使用 foreign_keys 指定外键
    store = relationship("Store", foreign_keys=[store_id])

    # 唯一约束
    __table_args__ = (
        {'mysql_engine': 'InnoDB'}
    )
