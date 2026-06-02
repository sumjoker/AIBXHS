from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from models.base import BaseModel


class ConversationHistory(BaseModel):
    """对话历史模型"""
    __tablename__ = "conversation_history"

    id = Column(Integer, primary_key=True, index=True, comment="记录ID")
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True, comment="用户ID")
    session_id = Column(String(64), nullable=False, index=True, comment="会话ID")
    role = Column(String(20), nullable=False, comment="角色")
    content = Column(Text, nullable=False, comment="内容")
    function_name = Column(String(100), nullable=True, comment="函数名称")
    chat_type = Column(String(20), default="review", index=True, comment="对话类型: review/inventory")
    is_deleted = Column(Boolean, default=False, nullable=False, index=True, comment="是否已删除")

    user = relationship("User")
