from sqlalchemy import Column, Integer, String, Float, Date, ForeignKey, Text, Boolean
from models.base import BaseModel


class InventorySnapshot(BaseModel):
    """库存快照表"""
    __tablename__ = "inventory_snapshots"

    id = Column(Integer, primary_key=True, index=True, comment="主键ID")
    tenant_id = Column(Integer, default=1, index=True, comment="租户ID")
    snapshot_date = Column(Date, nullable=False, index=True, comment="快照日期")

    # 汇总标记
    summary_flag = Column(String(10), default="0", comment="欧洲/北美汇总行标记")

    # 商品标识
    asin = Column(String(100), index=True, comment="ASIN")
    parent_asin = Column(String(1000), comment="父ASIN")
    msku = Column(String(2000), comment="MSKU")
    fnsku = Column(String(1000), comment="FNSKU")
    sku = Column(String(500), index=True, comment="SKU")
    product_name = Column(String(500), comment="品名")
    title = Column(String(1000), comment="标题")

    # 店铺与分类
    account = Column(String(500), index=True, comment="店铺")
    country = Column(String(200), index=True, comment="国家/地区")
    category = Column(String(200), comment="类目")
    brand = Column(String(200), comment="品牌")

    # 补货状态
    replenishment_status = Column(String(50), comment="补货状态")

    # 时间参数（天数）- 采购配置字段，Excel导入时不写入，保留字段供未来使用
    purchase_plan_days = Column(Integer, comment="采购计划天数（Excel不导入）")
    purchase_lead_time = Column(Integer, comment="采购交期（Excel不导入）")
    qc_days = Column(Integer, comment="质检天数（Excel不导入）")
    overseas_to_fba_days = Column(Integer, comment="海外仓至FBA天数（Excel不导入）")
    safety_days = Column(Integer, comment="安全天数（Excel不导入）")
    purchase_frequency = Column(Integer, comment="采购频率（Excel不导入）")
    local_ship_frequency = Column(Integer, comment="本地仓发货频率（Excel不导入）")
    overseas_ship_frequency = Column(Integer, comment="海外仓发货频率（Excel不导入）")
    stock_up_duration = Column(Integer, default=100, comment="备货时长（固定100天，Excel不导入）")

    # 销量数据
    sales_3d = Column(Float, comment="3天销量")
    sales_7d = Column(Float, comment="7天销量")
    sales_14d = Column(Float, comment="14天销量")
    sales_30d = Column(Float, comment="30天销量")
    sales_60d = Column(Float, comment="60天销量")
    sales_90d = Column(Float, comment="90天销量")

    # 日均销量
    daily_avg_3d = Column(Float, comment="3天日均销量")
    daily_avg_7d = Column(Float, comment="7天日均销量")
    daily_avg_14d = Column(Float, comment="14天日均销量")
    daily_avg_30d = Column(Float, comment="30天日均销量")
    daily_avg_60d = Column(Float, comment="60天日均销量")
    daily_avg_90d = Column(Float, comment="90天日均销量")

    # 可售天数
    days_supply_total = Column(Float, comment="可售天数总")
    days_supply_fba = Column(Float, comment="可售天数FBA")
    days_supply_fba_inbound = Column(Float, comment="可售天数FBA+在途")

    # 断货与预测
    stockout_date = Column(Date, nullable=True, comment="断货时间")
    daily_sales = Column(Float, comment="日均销量")
    sales_forecast = Column(Float, comment="销量预测")

    # FBA库存
    fba_stock = Column(Float, comment="FBA库存")
    fba_inbound = Column(Float, comment="FBA在途")
    fba_inbound_detail = Column(Text, comment="原始在途详情文本")
    fba_available = Column(Float, comment="可售")
    fba_pending_transfer = Column(Float, comment="待调仓")
    fba_in_transfer = Column(Float, comment="调仓中")
    fba_inbound_processing = Column(Float, comment="入库中")

    # 本地与总库存
    local_inventory = Column(Integer, default=0, comment="本地仓库存")
    # 海外仓（飞书多维表同步，仅写在主行；子行不落值，避免展开后重复计数）
    overseas_inventory = Column(Float, default=0, comment="海外仓可售库存")
    overseas_max_age = Column(Float, comment="海外仓最大库龄（天）")
    inspection_quantity = Column(Integer, default=0, comment="查验货件数量")
    total_stock = Column(Float, comment="总库存")

    # 库龄分布
    age_0_3 = Column(Float, comment="0-3个月库龄")
    age_3_6 = Column(Float, comment="3-6个月库龄")
    age_6_9 = Column(Float, comment="6-9个月库龄")
    age_9_12 = Column(Float, comment="9-12个月库龄")
    age_12_plus = Column(Float, comment="12个月以上库龄")
    gross_margin = Column(Float, comment="毛利率参考")

    # 退货率（小数，如 0.0712 表示 7.12%）
    # 导入策略与 gross_margin 完全一致：不在 NUMERIC_FIELDS 中，不做 fillna(0) 强转，
    # UPDATE 时跳过空值（保留库中原值），INSERT 时空值写 NULL
    return_rate_3d = Column(Float, comment="3天退货率")
    return_rate_7d = Column(Float, comment="7天退货率")
    return_rate_14d = Column(Float, comment="14天退货率")
    return_rate_30d = Column(Float, comment="30天退货率")
    return_rate_60d = Column(Float, comment="60天退货率")
    return_rate_90d = Column(Float, comment="90天退货率")

    # 节日产品标记（存储节日类型：""/"圣诞"/"万圣"/"跨年"/"其他"）
    is_holiday = Column(String(20), default="", comment="节日类型")
    # 停售产品标记
    is_discontinued = Column(Boolean, default=False, comment="停售产品标记")


class InboundShipmentDetail(BaseModel):
    """在途货件详情表"""
    __tablename__ = "inbound_shipment_details"

    id = Column(Integer, primary_key=True, index=True, comment="主键ID")
    tenant_id = Column(Integer, default=1, index=True, comment="租户ID")
    snapshot_id = Column(Integer, ForeignKey("inventory_snapshots.id"), index=True, comment="关联快照ID")

    asin = Column(String(100), index=True, comment="ASIN")
    account = Column(String(500), index=True, comment="店铺")
    country = Column(String(200), comment="国家/地区")

    shipment_id = Column(String(100), comment="货件单号")
    quantity = Column(Integer, comment="数量")
    logistics_method = Column(String(100), comment="物流方式")
    transport_method = Column(String(100), comment="运输方式")
    ship_date = Column(Date, nullable=True, comment="发货时间")
    estimated_available_date = Column(Date, nullable=True, comment="预计可售时间")
    estimated_arrival_date = Column(Date, nullable=True, comment="预计到港时间")
    raw_text = Column(Text, comment="原始行文本")


class ReplenishmentDecision(BaseModel):
    """补货决策表"""
    __tablename__ = "replenishment_decisions"

    id = Column(Integer, primary_key=True, index=True, comment="主键ID")
    tenant_id = Column(Integer, default=1, index=True, comment="租户ID")
    snapshot_id = Column(Integer, ForeignKey("inventory_snapshots.id"), index=True, comment="关联快照ID")

    summary_flag = Column(String(10), default="0", comment="欧洲/北美汇总行标记")
    asin = Column(String(100), index=True, comment="ASIN")
    sku = Column(String(500), index=True, comment="SKU")
    account = Column(String(500), index=True, comment="店铺")
    country = Column(String(200), index=True, comment="国家/地区")
    snapshot_date = Column(Date, nullable=False, index=True, comment="快照日期")

    # 决策数据
    future_stock = Column(Float, comment="未来可用库存")
    demand = Column(Float, comment="补货周期内需求预测量")
    safety_stock = Column(Float, comment="安全库存量")
    suggest_qty = Column(Float, comment="建议补货数量")
    days_of_supply = Column(Float, comment="可售天数")
    stockout_days = Column(Float, comment="预计断货天数")
    stockout_date_calc = Column(String(20), comment="断货时间（计算得出）")
    risk_level = Column(String(10), comment="风险等级: 红/黄/绿")
    reason = Column(String(1000), comment="补货建议原因说明")

    # 产品状态标记（与 inventory_snapshots 同步）
    is_holiday = Column(String(20), default="", comment="节日类型")
    is_discontinued = Column(Boolean, default=False, comment="停售产品标记")

    # 标签系统（M4 过滤引擎产出；真源在 product_tags，此处为决策行冗余口径）
    # 快照表显式不加列（避免 Excel 导入 UPSERT 覆盖标签口径）
    force_replenish = Column(Integer, default=0, comment="强制补货标识（override 生效）")
    filter_status = Column(String(30), default="KEEP", comment="展示口径：KEEP/DISCONTINUED/NEG_MARGIN_EXCLUDED/HOLIDAY_EXCLUDED")
