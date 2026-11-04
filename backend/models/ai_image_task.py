from sqlalchemy import Column, Integer, String, Text, DateTime
from models.base import BaseModel


class AIImageTask(BaseModel):
    """AI 商品套图生成任务表（Qwen-Image-2.1 图生图，ComfyUI API）

    一条记录对应一次「生成套图」：参考图、商品信息、生成类型组合、比例与张数，
    以及逐张生成结果 [{type,url,status}]。生成过程由后台线程提交工作流并轮询回写。
    """
    __tablename__ = "ai_image_tasks"

    id = Column(Integer, primary_key=True, index=True, comment="任务ID")
    tenant_id = Column(Integer, nullable=False, default=0, index=True, comment="租户ID")
    user_id = Column(Integer, nullable=False, index=True, comment="提交用户ID")
    creator_name = Column(String(100), nullable=True, comment="生成者用户名")
    title = Column(String(200), nullable=True, comment="任务标题（产品名）")
    product_code = Column(String(50), nullable=True, index=True, comment="产品编码（从产品管理选图时记录）")
    product_name = Column(String(200), nullable=True, comment="产品品名")
    status = Column(String(20), nullable=False, default="排队中", index=True,
                    comment="状态：排队中/生成中/已完成/部分完成/失败/已取消")
    provider = Column(String(20), nullable=False, default="local",
                      comment="生成通道：local=本地Qwen Image 2.1 / relay=中转站 Nano Banana 2")

    # 生成参数
    generation_types = Column(Text, nullable=True,
                              comment="生成类型列表（JSON 数组）：白底图/场景图/特写图/卖点图/A+图")
    aspect_ratio = Column(String(20), nullable=True, comment="输出比例：1:1/3:4/4:3/2:3/3:2")
    image_count = Column(Integer, nullable=False, default=1, comment="每种类型生成张数")
    product_info = Column(Text, nullable=True, comment="商品信息（卖点/材质/场景等，AI 分析或手填）")
    prompt = Column(Text, nullable=True, comment="附加提示词（用户自定义）")

    images = Column(Text, nullable=True, comment="参考图地址列表（JSON 数组，TOS 公网地址）")

    # ===== v2（2026-10-15）：A+ 与主副图分组参数 + 品牌自定义设置 =====
    aplus_type = Column(String(30), nullable=True,
                        comment="A+ 类型（单选）：aplus_advanced=高级A+(1464:600) / aplus_normal=普通A+(970:600) / aplus_mobile=手机A+(600:450)")
    main_types = Column(Text, nullable=True,
                        comment="主副图类型列表（JSON 数组）：selling_point/white_background/scene/closeup/closeup_white")
    main_aspect_ratio = Column(String(20), nullable=True, comment="主副图生成比例：1:1/3:4/4:3/9:16/16:9")
    brand_settings = Column(Text, nullable=True,
                            comment="品牌自定义设置（JSON）：main_color/font_style/custom_text/country/language/platform")

    # 结果：JSON 数组 [{type, url, status, error}]，status: 排队中/生成中/已完成/失败
    result_images = Column(Text, nullable=True, comment="生成结果（JSON 数组，按类型逐张）")
    comfy_prompt_ids = Column(Text, nullable=True, comment="ComfyUI 任务ID列表（JSON 数组，恢复轮询用）")

    started_at = Column(DateTime(timezone=True), nullable=True, comment="开始生成时间（首个子任务进入生成中）")
    error_message = Column(String(1000), nullable=True, comment="失败原因")
    finished_at = Column(DateTime(timezone=True), nullable=True, comment="完成时间")
