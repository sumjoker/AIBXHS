from sqlalchemy import Column, Integer, String, Enum, DateTime, ForeignKey, JSON, Boolean, Text, SmallInteger, Float
from sqlalchemy.orm import relationship
from models.base import BaseModel
import enum


class ReviewStatus(str, enum.Enum):
    NEW = "new"
    READ = "read"
    PROCESSING = "processing"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class Sentiment(str, enum.Enum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


class HandlingAction(str, enum.Enum):
    READ = "read"
    TAG = "tag"
    COMMENT = "comment"
    REPLY = "reply"
    DISMISS = "dismiss"
    OTHER = "other"


class ImportanceLevel(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Review(BaseModel):
    """评论模型"""
    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True, index=True, comment="评论ID")
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True, comment="租户ID")
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False, index=True, comment="店铺ID")
    asin = Column(String(20), nullable=True, index=True, comment="产品ASIN")
    reviewer_name = Column(String(200), nullable=True, comment="买家名字")
    rating = Column(SmallInteger, nullable=False, index=True, comment="星级评分(1-5)")
    title = Column(String(500), nullable=True, comment="评论主题")
    content = Column(Text, nullable=False, comment="评论原文")
    translated_title = Column(String(500), nullable=True, comment="翻译后的标题")
    translated_content = Column(Text, nullable=True, comment="翻译后的内容")
    review_date = Column(DateTime, nullable=False, index=True, comment="评论日期")
    crawled_at = Column(DateTime, nullable=True, comment="抓取日期")
    account = Column(String(100), nullable=True, comment="账号")
    site = Column(String(50), nullable=True, comment="站点")
    return_rate = Column(Float, nullable=True, comment="退货率")
    status = Column(Enum(ReviewStatus), default=ReviewStatus.NEW, nullable=True, index=True, comment="处理状态")
    importance_level = Column(Enum(ImportanceLevel), default=ImportanceLevel.MEDIUM, nullable=True, index=True, comment="重要等级")

    # 关联关系
    tenant = relationship("Tenant", back_populates="reviews")
    # Store 已简化，移除 back_populates
    store = relationship("Store", foreign_keys=[store_id])
    analysis = relationship("ReviewAnalysis", back_populates="review", uselist=False, cascade="all, delete-orphan")
    handlings = relationship("ReviewHandling", back_populates="review", cascade="all, delete-orphan")


class ReviewAnalysis(BaseModel):
    """评论分析模型"""
    __tablename__ = "review_analyses"

    id = Column(Integer, primary_key=True, index=True, comment="分析ID")
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True, comment="租户ID")
    review_id = Column(Integer, ForeignKey("reviews.id"), nullable=False, unique=True, comment="评论ID")
    model = Column(String(100), nullable=True, comment="AI模型")
    sentiment = Column(Enum(Sentiment), nullable=False, index=True, comment="情感分析")
    sentiment_score = Column(Integer, nullable=True, comment="情感分数")
    key_points = Column(JSON, nullable=True, comment="核心观点")
    topics = Column(JSON, nullable=True, comment="主题分类")
    suggestions = Column(JSON, nullable=True, comment="处理建议")
    summary = Column(Text, nullable=True, comment="分析摘要")
    raw_response = Column(Text, nullable=True, comment="AI原始响应")
    analysis_time = Column(Integer, nullable=True, comment="分析耗时(ms)")

    # 关联关系
    review = relationship("Review", back_populates="analysis")


class ReviewHandling(BaseModel):
    """评论处理记录模型"""
    __tablename__ = "review_handlings"

    id = Column(Integer, primary_key=True, index=True, comment="处理记录ID")
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True, comment="租户ID")
    review_id = Column(Integer, ForeignKey("reviews.id"), nullable=False, index=True, comment="评论ID")
    handler_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True, comment="处理人")
    action = Column(Enum(HandlingAction), nullable=False, comment="操作类型")
    note = Column(Text, nullable=True, comment="处理备注")
    reply_content = Column(Text, nullable=True, comment="回复内容")
    reply_sent = Column(Boolean, default=False, comment="回复是否已发送")
    reply_sent_at = Column(DateTime, nullable=True, comment="回复发送时间")

    # 关联关系
    review = relationship("Review", back_populates="handlings")
