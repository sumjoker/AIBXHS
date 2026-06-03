import json
import asyncio
import threading
from typing import AsyncGenerator, Optional
from datetime import datetime
from queue import Queue, Empty
from openai import OpenAI
from sqlalchemy.orm import Session
from config import get_settings
from services.chat_service import (
    query_inventory_status, query_negative_reviews,
    analyze_and_save_single_review, get_review_analysis,
    save_message, get_conversation_history
)
import logging

logger = logging.getLogger(__name__)
settings = get_settings()


class StreamingService:
    """流式响应服务"""

    def __init__(self):
        self.client = OpenAI(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_API_BASE,
            max_retries=3,
            timeout=120.0,
        ) if settings.OPENAI_API_KEY else None

    def stream_chat_response(
        self,
        db: Session,
        user_id: int,
        session_id: str,
        user_message: str,
        chat_type: str = "review"
    ):
        """
        生成流式聊天响应（同步版本）

        Yields:
            SSE格式的数据字符串
        """
        if not self.client:
            yield self._format_sse("error", "OpenAI API Key 未配置")
            return

        try:
            # 保存用户消息
            save_message(db, user_id, session_id, "user", user_message, chat_type=chat_type)

            # 获取对话历史
            history = get_conversation_history(db, user_id, session_id, limit=10)

            # 根据类型选择系统提示词
            system_prompt = self._get_system_prompt(chat_type)

            # 构建消息列表
            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(history)
            messages.append({"role": "user", "content": user_message})

            # 获取工具定义
            tools = self._get_tools(chat_type)

            if tools:
                # 首先进行工具调用判断（非流式）
                try:
                    tool_response = self.client.chat.completions.create(
                        model=settings.OPENAI_MODEL,
                        messages=messages,
                        tools=tools,
                        tool_choice="auto",
                        timeout=120
                    )

                    assistant_message = tool_response.choices[0].message

                    if assistant_message.tool_calls:
                        # 处理工具调用
                        for chunk in self._handle_tool_calls_sync(
                            db, user_id, session_id, user_message,
                            messages, assistant_message, chat_type
                        ):
                            yield chunk
                        return
                except Exception as e:
                    logger.error(f"工具调用失败: {e}")
                    # 429限流或连接错误时，降级为普通流式回复
                    if "429" in str(e) or "Connection error" in str(e):
                        logger.info("工具调用因限流/连接失败，降级为普通流式回复")
                    else:
                        yield self._format_sse("error", f"处理请求失败: {str(e)}")
                        return

            # 直接流式生成回复
            for chunk in self._generate_streaming_response_sync(
                db, user_id, session_id, messages, chat_type
            ):
                yield chunk

        except Exception as e:
            logger.error(f"流式响应错误: {e}")
            yield self._format_sse("error", str(e))

    def _generate_streaming_response_sync(
        self,
        db: Session,
        user_id: int,
        session_id: str,
        messages: list,
        chat_type: str
    ):
        """同步流式生成回复"""
        try:
            # 发送开始标记
            yield self._format_sse("start", "")

            # 创建流式请求
            stream = self.client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                messages=messages,
                stream=True,
                temperature=0.7,
                timeout=300
            )

            full_content = ""

            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    full_content += content
                    yield self._format_sse("content", content)

            # 保存完整回复
            if full_content:
                save_message(db, user_id, session_id, "assistant", full_content, chat_type=chat_type)

            # 发送结束标记
            yield self._format_sse("done", "", {"session_id": session_id})

        except Exception as e:
            logger.error(f"流式生成错误: {e}")
            yield self._format_sse("error", f"生成回复失败: {str(e)}")

    def _handle_tool_calls_sync(
        self,
        db: Session,
        user_id: int,
        session_id: str,
        user_message: str,
        messages: list,
        assistant_message,
        chat_type: str
    ):
        """同步处理工具调用"""
        for tool_call in assistant_message.tool_calls:
            function_name = tool_call.function.name
            arguments = json.loads(tool_call.function.arguments)

            yield self._format_sse("thinking", "正在分析您的问题...")

            # 执行工具调用
            if function_name == "query_inventory_status":
                result = self._execute_inventory_query(db, arguments)
            elif function_name == "parse_date_range":
                result = self._execute_review_query(db, arguments)
            else:
                result = {"error": "未知工具"}

            yield self._format_sse("thinking", f"查询完成，正在生成回复...")

            # 添加工具调用结果到消息
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": function_name,
                        "arguments": tool_call.function.arguments
                    }
                }]
            })
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(result, ensure_ascii=False)
            })

            # 流式生成最终回复
            for chunk in self._generate_streaming_response_sync(
                db, user_id, session_id, messages, chat_type
            ):
                yield chunk

    def _execute_inventory_query(self, db: Session, arguments: dict) -> dict:
        """执行库存查询"""
        query_type = arguments.get("query_type", "all")
        risk_level = arguments.get("risk_level")
        limit = arguments.get("limit", 10)

        try:
            items = query_inventory_status(db, query_type, risk_level, limit)
            return {"items": items, "count": len(items)}
        except Exception as e:
            logger.error(f"库存查询失败: {e}")
            return {"error": str(e), "items": [], "count": 0}

    def _execute_review_query(self, db: Session, arguments: dict) -> dict:
        """执行差评查询"""
        start_date = arguments.get("start_date")
        end_date = arguments.get("end_date")

        if not start_date or not end_date:
            end_date = datetime.now().strftime("%Y-%m-%d")
            start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        try:
            reviews = query_negative_reviews(db, start_date, end_date)

            # 自动分析评论
            analyzed_reviews = []
            for review in reviews[:10]:  # 限制分析数量
                analysis = get_review_analysis(db, review["id"])
                if not analysis:
                    analysis = analyze_and_save_single_review(db, review)

                analyzed_reviews.append({
                    "product_name": review.get("product_name", review["asin"]),
                    "rating": review["rating"],
                    "analysis": analysis
                })

            return {"reviews": analyzed_reviews, "count": len(reviews)}
        except Exception as e:
            logger.error(f"差评查询失败: {e}")
            return {"error": str(e), "reviews": [], "count": 0}

    def _get_system_prompt(self, chat_type: str) -> str:
        """获取系统提示词"""
        current_date = datetime.now().strftime("%Y-%m-%d")

        if chat_type == "inventory":
            return f"""你是专业的跨境电商库存分析助手。当前日期: {current_date}。

你的能力：
- 查询库存状态、断货风险商品、补货建议
- 分析库存健康度，给出补货优先级建议

重要规则：
- 使用商品的【真实名称】来引用产品，不要只使用ASIN
- 回复要简洁专业，突出关键数据和建议
- 优先展示风险等级和建议补货数量"""
        else:
            return f"""你是专业的跨境电商差评分析助手。当前日期: {current_date}。

任务：
1. 解析用户提到的日期范围
2. 查询该日期范围内的差评
3. 用中文进行分析回复

重要规则：
- 绝对不要在回复中使用数字ID或ASIN编号来标识产品
- 必须使用商品的【真实名称】来引用产品"""

    def _get_tools(self, chat_type: str) -> list:
        """获取工具定义"""
        if chat_type == "inventory":
            return [{
                "type": "function",
                "function": {
                    "name": "query_inventory_status",
                    "description": "查询库存状态",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query_type": {
                                "type": "string",
                                "enum": ["stockout_risk", "need_restock", "low_stock", "all"]
                            },
                            "risk_level": {"type": "string", "enum": ["red", "yellow", "green"]},
                            "limit": {"type": "integer", "default": 10}
                        },
                        "required": ["query_type"]
                    }
                }
            }]
        else:
            return [{
                "type": "function",
                "function": {
                    "name": "parse_date_range",
                    "description": "解析日期范围",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "start_date": {"type": "string"},
                            "end_date": {"type": "string"},
                            "date_description": {"type": "string"}
                        },
                        "required": ["start_date", "end_date"]
                    }
                }
            }]

    def _format_sse(self, event_type: str, data: str, extra: dict = None) -> str:
        """格式化SSE消息"""
        payload = {"type": event_type, "content": data}
        if extra:
            payload.update(extra)
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# 添加缺失的导入
from datetime import timedelta
