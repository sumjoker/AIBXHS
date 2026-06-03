"""
聊天功能相关的 Pydantic Schema 模型

该模块定义了库存机器人项目中 AI 对话功能的所有请求和响应模型，
包括标准聊天、流式响应、会话管理、搜索和导出等功能的数据验证。
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from datetime import datetime


class ChatRequest(BaseModel):
    """聊天请求模型
    
    用于标准聊天接口和流式聊天接口的请求参数验证。
    
    Attributes:
        message: 用户输入的消息内容，必填，长度限制1-4000字符
        session_id: 会话ID，可选，用于保持对话上下文
        chat_type: 对话类型，可选值为 "review"(差评分析) 或 "inventory"(库存分析)
        stream: 是否使用流式响应，默认为False
    """
    message: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        description="用户消息"
    )
    session_id: Optional[str] = Field(
        None,
        max_length=64,
        description="会话ID"
    )
    chat_type: Literal["review", "inventory"] = Field(
        "review",
        description="对话类型: review=差评分析, inventory=库存分析"
    )
    stream: bool = Field(
        False,
        description="是否使用流式响应"
    )


class ChatResponse(BaseModel):
    """聊天响应模型
    
    用于标准聊天接口的响应数据。
    
    Attributes:
        reply: AI生成的回复内容
        session_id: 当前会话ID
        message_id: 消息在数据库中的记录ID，可选
        created_at: 消息创建时间，可选
    """
    reply: str = Field(
        ...,
        description="AI回复内容"
    )
    session_id: str = Field(
        ...,
        description="会话ID"
    )
    message_id: Optional[int] = Field(
        None,
        description="消息ID"
    )
    created_at: Optional[datetime] = Field(
        None,
        description="创建时间"
    )


class StreamingChunk(BaseModel):
    """流式响应数据块模型
    
    用于SSE(Server-Sent Events)流式响应的数据格式。
    
    Attributes:
        type: 数据块类型，可选值为 "content"(内容)、"done"(完成)、"error"(错误)
        content: 内容片段，type为"content"时有效
        session_id: 会话ID
        error: 错误信息，type为"error"时有效
    """
    type: Literal["content", "done", "error", "start", "thinking"] = Field(
        ...,
        description="数据块类型: content=内容, done=完成, error=错误, start=开始, thinking=思考中"
    )
    content: Optional[str] = Field(
        None,
        description="内容片段"
    )
    session_id: Optional[str] = Field(
        None,
        description="会话ID"
    )
    error: Optional[str] = Field(
        None,
        description="错误信息"
    )


class ChatSessionResponse(BaseModel):
    """会话响应模型
    
    用于返回会话列表项的数据结构。
    
    Attributes:
        id: 记录ID
        session_id: 会话唯一标识
        title: 会话标题（通常取自第一条用户消息的前30字符）
        created_at: 会话创建时间
        message_count: 该会话中的消息数量
    """
    id: int = Field(
        ...,
        description="记录ID"
    )
    session_id: str = Field(
        ...,
        description="会话ID"
    )
    title: str = Field(
        ...,
        description="会话标题"
    )
    created_at: str = Field(
        ...,
        description="创建时间"
    )
    message_count: Optional[int] = Field(
        0,
        description="消息数量"
    )


class ChatMessageResponse(BaseModel):
    """消息响应模型
    
    用于返回单条消息的数据结构。
    
    Attributes:
        id: 消息ID
        role: 消息角色，可选值为 "user"(用户)、"assistant"(AI助手)、"system"(系统)
        content: 消息内容
        created_at: 消息创建时间
        function_name: 调用的函数名（如果是工具调用消息）
    """
    id: int = Field(
        ...,
        description="消息ID"
    )
    role: Literal["user", "assistant", "system"] = Field(
        ...,
        description="角色: user=用户, assistant=AI助手, system=系统"
    )
    content: str = Field(
        ...,
        description="内容"
    )
    created_at: str = Field(
        ...,
        description="创建时间"
    )
    function_name: Optional[str] = Field(
        None,
        description="调用的函数名"
    )


class ChatSearchRequest(BaseModel):
    """搜索请求模型
    
    用于搜索对话历史的请求参数。
    
    Attributes:
        query: 搜索关键词，必填，长度限制1-100字符
        chat_type: 对话类型过滤，可选
        limit: 返回数量限制，范围1-50，默认10
    """
    query: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="搜索关键词"
    )
    chat_type: Optional[Literal["review", "inventory"]] = Field(
        None,
        description="对话类型过滤"
    )
    limit: int = Field(
        10,
        ge=1,
        le=50,
        description="返回数量限制"
    )


class ChatExportRequest(BaseModel):
    """导出请求模型
    
    用于导出对话记录的请求参数。
    
    Attributes:
        session_id: 要导出的会话ID，必填
        format: 导出格式，可选值为 "markdown"、"json"、"txt"，默认"markdown"
    """
    session_id: str = Field(
        ...,
        description="会话ID"
    )
    format: Literal["markdown", "json", "txt"] = Field(
        "markdown",
        description="导出格式: markdown, json, txt"
    )
