from sqlalchemy import Column, Integer, String, Float, Date, UniqueConstraint
from models.base import BaseModel


class OverseasInventory(BaseModel):
    """海外仓库存表 - 飞书多维表同步（SKU + 品名为唯一键，覆盖式全量同步）

    注：sku / product_name 用 VARCHAR(255) 而非 500，
    保证 (tenant_id, sku, product_name) 联合唯一索引不超过 InnoDB 3072 字节上限
    （utf8mb4 下 (255+255)*4 = 2040 字节），源表实际最长 SKU 12 字符、品名 43 字符。
    """

    __tablename__ = "overseas_inventories"

    id = Column(Integer, primary_key=True, index=True, comment="主键ID")
    tenant_id = Column(Integer, default=1, index=True, comment="租户ID")

    # 业务唯一键：SKU + 品名
    sku = Column(String(255), nullable=False, comment="SKU")
    product_name = Column(String(255), nullable=False, comment="品名（飞书 SKU名称）")

    # 库存数据
    site = Column(String(100), comment="站点（美国/欧洲等）")
    available_qty = Column(Float, default=0, comment="可售数量")
    max_age = Column(Float, comment="最大库龄（天）")

    # 数据来源信息
    record_date = Column(Date, index=True, comment="源数据日期（飞书「日期」字段）")
    feishu_record_id = Column(String(100), comment="飞书记录ID")

    __table_args__ = (
        UniqueConstraint("tenant_id", "sku", "product_name", name="uq_overseas_sku_name"),
    )
