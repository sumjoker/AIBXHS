from sqlalchemy import Column, Integer, String, Float, Date, Index
from models.base import BaseModel


class LocalInventory(BaseModel):
    """本地仓库存表 - 运营上传"""
    __tablename__ = "local_inventories"

    id = Column(Integer, primary_key=True, index=True, comment="主键ID")
    tenant_id = Column(Integer, default=1, index=True, comment="租户ID")

    # 商品标识
    asin = Column(String(100), index=True, comment="ASIN")
    sku = Column(String(500), index=True, comment="SKU")
    product_name = Column(String(500), comment="品名")
    account = Column(String(500), index=True, comment="店铺")
    country = Column(String(200), comment="国家/地区")

    # 库存数量
    quantity = Column(Float, default=0, comment="本地仓库存数量")

    # 上传批次
    batch_date = Column(Date, index=True, comment="上传批次日期")

    __table_args__ = (
        Index('ix_local_inv_asin_account', 'asin', 'account'),
    )
