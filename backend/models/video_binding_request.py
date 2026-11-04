from sqlalchemy import Column, Integer, String, DateTime
from models.base import BaseModel


class VideoBindingRequest(BaseModel):
    """AI视频/高清处理「绑定至产品」审核请求"""
    __tablename__ = "video_binding_requests"
    # 固化排序规则与业务表一致，避免跨表 URL 比较时 MySQL 报 1267 排序规则冲突
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    id = Column(Integer, primary_key=True, index=True, comment="请求ID")
    tenant_id = Column(Integer, nullable=False, index=True, comment="租户ID")
    video_url = Column(String(1000), nullable=False, comment="媒体公网地址（TOS，video 存视频 / image 存图片）")
    media_type = Column(String(10), nullable=False, default="video", comment="媒体类型：video=视频 / image=图片")
    product_id = Column(Integer, nullable=False, index=True, comment="目标产品ID")
    product_code = Column(String(100), nullable=True, comment="产品编码快照")
    product_name = Column(String(200), nullable=True, comment="产品名称快照")
    source = Column(String(20), default="ai_video", comment="来源：ai_video=AI视频 / upscale=高清处理")
    task_title = Column(String(200), nullable=True, comment="来源任务标题")
    status = Column(String(20), default="pending", index=True, comment="状态：pending=待审核 / approved=已通过 / rejected=已拒绝")
    requester_id = Column(Integer, nullable=True, comment="提交人ID")
    requester_name = Column(String(100), nullable=True, comment="提交人用户名")
    reject_reason = Column(String(500), nullable=True, comment="拒绝原因")
    reviewer_id = Column(Integer, nullable=True, comment="审核人ID")
    reviewer_name = Column(String(100), nullable=True, comment="审核人用户名")
    reviewed_at = Column(DateTime, nullable=True, comment="审核时间")
