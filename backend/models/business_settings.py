from sqlalchemy import Column, Integer, String, Float, Text, DateTime
from models.base import BaseModel


class BusinessSettings(BaseModel):
    """业务设置表 - 存储公式配置"""
    __tablename__ = "business_settings"

    id = Column(Integer, primary_key=True, index=True, comment="主键ID")
    tenant_id = Column(Integer, default=1, index=True, comment="租户ID")

    # 设置类型：daily_sales=日均销量公式
    setting_type = Column(String(50), nullable=False, index=True, comment="设置类型")
    setting_name = Column(String(100), nullable=False, comment="设置名称")

    # 公式配置（JSON格式存储各权重）
    formula_config = Column(Text, nullable=False, comment="公式配置JSON")

    # 是否启用
    is_active = Column(Integer, default=1, comment="是否启用：0=禁用，1=启用")

    # 继承 BaseModel 的 created_at, updated_at, deleted_at
