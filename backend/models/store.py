from sqlalchemy import Column, Integer, String, Enum, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from models.base import BaseModel
import enum


class Platform(str, enum.Enum):
    AMAZON = "amazon"
    SHOPEE = "shopee"
    LAZADA = "lazada"
    TIKTOK = "tiktok"
    OTHER = "other"


class StoreStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    ERROR = "error"


class Store(BaseModel):
    """店铺模型"""
    __tablename__ = "stores"

    id = Column(Integer, primary_key=True, index=True, comment="店铺ID")
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True, comment="租户ID")
    name = Column(String(100), nullable=False, comment="店铺名称")
    platform = Column(String(20), nullable=False, index=True, comment="平台")
    site = Column(String(50), nullable=True, comment="站点")
    status = Column(String(20), default="active", index=True, comment="状态")
    department_id = Column(Integer, ForeignKey("departments.id"), nullable=True, index=True, comment="所属部门ID")
    inventory_name = Column(String(100), nullable=True, index=True, comment="库存数据中的店铺名别名")

    # 关联关系
    tenant = relationship("Tenant", back_populates="stores")
