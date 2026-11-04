from sqlalchemy import Column, Integer, String, Boolean, Text
from models.base import BaseModel


class AIImageLayout(BaseModel):
    """A+ 版式素材（用户上传的版式参考图 / 内置版式的收藏记录）

    内置版式本身写在 backend/prompts/aplus_layouts.json，不落库；
    本表只存两类行：
    - source='upload'：用户上传的版式参考图（image_url 指向 TOS），用于「我的」
    - source='favorite'：用户对内置版式的收藏，preset_id 指向 json 里的 id
    删除一律软删（deleted_at），历史任务仍能回看当时用的版式名。
    """
    __tablename__ = "ai_image_layouts"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    id = Column(Integer, primary_key=True, index=True, comment="素材ID")
    tenant_id = Column(Integer, nullable=False, default=0, index=True, comment="租户ID")
    user_id = Column(Integer, nullable=False, index=True, comment="所属用户ID")
    source = Column(String(10), nullable=False, default="upload",
                    comment="来源：upload=用户上传 / favorite=内置版式收藏")
    preset_id = Column(String(50), nullable=True, index=True,
                       comment="内置版式ID（source=favorite 时有效，对应 aplus_layouts.json）")
    name = Column(String(100), nullable=True, comment="版式名称")
    image_url = Column(String(1000), nullable=True, comment="版式参考图公网地址（TOS，生成时用）")
    thumb_url = Column(String(1000), nullable=True, comment="列表缩略图地址（TOS，≤480px，仅展示）")
    ratio_tag = Column(String(20), nullable=True, comment="画幅：horizontal/vertical/square")
    width = Column(Integer, nullable=True, comment="原图宽")
    height = Column(Integer, nullable=True, comment="原图高")
    structure = Column(Text, nullable=True, comment="版式结构描述（上传版式为人工/自动补充，可为空）")
    favorite = Column(Boolean, nullable=False, default=False, comment="是否收藏（上传素材也支持收藏）")
    used_count = Column(Integer, nullable=False, default=0, comment="被使用次数（排序用）")
