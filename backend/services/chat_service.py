import json
import uuid
import sys
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import text

import models.user  # noqa
import models.tenant  # noqa
import models.store  # noqa
import models.product  # noqa
import models.review  # noqa

from models.conversation import ConversationHistory
from models.review import Review, ReviewAnalysis, Sentiment
from openai import OpenAI
from config import get_settings

settings = get_settings()

# 配置日志
logger = logging.getLogger(__name__)

client = OpenAI(
    api_key=settings.OPENAI_API_KEY,
    base_url=settings.OPENAI_API_BASE
) if settings.OPENAI_API_KEY else None

DATE_PARSING_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "parse_date_range",
            "description": "解析用户提到的日期范围，返回开始日期和结束日期",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "开始日期，格式 YYYY-MM-DD"
                    },
                    "end_date": {
                        "type": "string",
                        "description": "结束日期，格式 YYYY-MM-DD"
                    },
                    "date_description": {
                        "type": "string",
                        "description": "对日期范围的描述，如'最近一个月'、'2024年全年'等"
                    }
                },
                "required": ["start_date", "end_date", "date_description"]
            }
        }
    }
]

# 库存查询工具
INVENTORY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "query_inventory_status",
            "description": "查询库存状态，可按风险等级、补货需求等条件筛选。支持查询：断货风险商品、需要补货的商品、库存正常商品等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query_type": {
                        "type": "string",
                        "enum": ["stockout_risk", "need_restock", "low_stock", "all"],
                        "description": "查询类型：stockout_risk=断货风险商品，need_restock=需要补货的商品，low_stock=低库存商品，all=全部库存"
                    },
                    "risk_level": {
                        "type": "string",
                        "enum": ["red", "yellow", "green"],
                        "description": "风险等级筛选：red=断货风险，yellow=库存预警，green=库存正常"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回数量限制，默认10条",
                        "default": 10
                    }
                },
                "required": ["query_type"]
            }
        }
    }
]

# 合并所有工具
ALL_TOOLS = DATE_PARSING_TOOLS + INVENTORY_TOOLS


def query_inventory_status(db: Session, query_type: str, risk_level: str = None, limit: int = 10) -> List[Dict[str, Any]]:
    """查询库存状态"""
    try:
        from models.restock import InventorySnapshot, ReplenishmentDecision
        from sqlalchemy import func

        # 获取最新快照日期
        latest = db.query(func.max(InventorySnapshot.snapshot_date)).scalar()
        if not latest:
            return []

        # 构建基础查询
        query = db.query(InventorySnapshot, ReplenishmentDecision).outerjoin(
            ReplenishmentDecision,
            (ReplenishmentDecision.snapshot_id == InventorySnapshot.id) &
            (ReplenishmentDecision.snapshot_date == latest)
        ).filter(
            InventorySnapshot.snapshot_date == latest,
            (InventorySnapshot.summary_flag != "共享库存") | (InventorySnapshot.summary_flag.is_(None))
        )

        # 根据查询类型筛选
        if query_type == "stockout_risk":
            query = query.filter(ReplenishmentDecision.risk_level == "红")
            query = query.order_by(ReplenishmentDecision.days_of_supply.asc())
        elif query_type == "need_restock":
            query = query.filter(ReplenishmentDecision.suggest_qty > 0)
            query = query.order_by(ReplenishmentDecision.suggest_qty.desc())
        elif query_type == "low_stock":
            query = query.filter(ReplenishmentDecision.days_of_supply <= 60)
            query = query.order_by(ReplenishmentDecision.days_of_supply.asc())
        elif risk_level:
            risk_map = {"red": "红", "yellow": "黄", "green": "绿"}
            query = query.filter(ReplenishmentDecision.risk_level == risk_map.get(risk_level, risk_level))

        results = query.limit(limit).all()

        items = []
        for snap, dec in results:
            items.append({
                "asin": snap.asin or "",
                "sku": snap.sku or "",
                "product_name": snap.product_name or snap.asin or "未知商品",
                "account": snap.account or "",
                "country": snap.country or "",
                "fba_stock": int(snap.fba_stock) if snap.fba_stock else 0,
                "fba_available": int(snap.fba_available) if snap.fba_available else 0,
                "fba_inbound": int(snap.fba_inbound) if snap.fba_inbound else 0,
                "daily_sales": round(float(snap.daily_sales), 1) if snap.daily_sales else 0,
                "days_of_supply": round(float(dec.days_of_supply), 1) if dec and dec.days_of_supply else 0,
                "suggest_qty": int(dec.suggest_qty) if dec and dec.suggest_qty else 0,
                "risk_level": dec.risk_level if dec else "绿",
                "stockout_date": dec.stockout_date_calc if dec else "-",
                "reason": dec.reason if dec else "",
            })

        return items

    except Exception as e:
        logger.error(f"查询库存状态失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return []


def query_negative_reviews(db: Session, start_date: str, end_date: str, asin: Optional[str] = None) -> List[Dict[str, Any]]:
    """查询差评工具函数 - 使用纯SQL避免Enum问题"""
    try:
        start_date_obj = datetime.strptime(start_date, "%Y-%m-%d")
        end_date_obj = datetime.strptime(end_date, "%Y-%m-%d")

        # 明确查询产品表的 name 字段作为产品名
        if asin:
            query = text("""
                SELECT r.id, r.asin, r.reviewer_name, r.rating, r.title, r.content, 
                       r.translated_content, r.review_date, r.crawled_at, r.account, 
                       r.site, r.return_rate, r.tenant_id,
                       CASE 
                           WHEN p.name IS NOT NULL AND p.name != '' THEN p.name
                           WHEN r.asin IS NOT NULL AND r.asin != '' THEN r.asin
                           ELSE '未知商品' 
                       END as product_name
                FROM reviews r
                LEFT JOIN products p ON r.asin = p.asin
                WHERE r.rating <= 3
                AND r.review_date >= :start_date
                AND r.review_date <= :end_date
                AND r.asin = :asin
                ORDER BY r.review_date DESC
                LIMIT 50
            """)
            result = db.execute(query, {
                "start_date": start_date_obj, "end_date": end_date_obj, "asin": asin
            })
        else:
            query = text("""
                SELECT r.id, r.asin, r.reviewer_name, r.rating, r.title, r.content, 
                       r.translated_content, r.review_date, r.crawled_at, r.account, 
                       r.site, r.return_rate, r.tenant_id,
                       CASE 
                           WHEN p.name IS NOT NULL AND p.name != '' THEN p.name
                           WHEN r.asin IS NOT NULL AND r.asin != '' THEN r.asin
                           ELSE '未知商品' 
                       END as product_name
                FROM reviews r
                LEFT JOIN products p ON r.asin = p.asin
                WHERE r.rating <= 3
                AND r.review_date >= :start_date
                AND r.review_date <= :end_date
                ORDER BY r.review_date DESC
                LIMIT 50
            """)
            result = db.execute(query, {
                "start_date": start_date_obj, "end_date": end_date_obj
            })

        reviews = result.fetchall()
        logger.debug(f"[DB] 查询到 {len(reviews)} 条差评")

        result_data = []
        for row in reviews:
            result_data.append({
                "id": row[0],
                "asin": row[1],
                "reviewer_name": row[2],
                "rating": row[3],
                "title": row[4],
                "content": row[5],
                "translated_content": row[6],
                "review_date": row[7].strftime("%Y-%m-%d") if row[7] else "",
                "crawled_at": row[8].strftime("%Y-%m-%d") if row[8] else "",
                "account": row[9],
                "site": row[10],
                "return_rate": row[11],
                "tenant_id": row[12],
                "product_name": row[13]
            })
        
        return result_data
    except Exception as e:
        logger.error(f"查询差评出错: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return []


def get_review_analysis(db: Session, review_id: int) -> Optional[dict]:
    """从数据库获取评论分析结果"""
    query = text("""
        SELECT id, tenant_id, review_id, model, sentiment, sentiment_score,
               key_points, topics, suggestions, summary, raw_response
        FROM review_analyses
        WHERE review_id = :review_id
    """)
    result = db.execute(query, {"review_id": review_id})
    row = result.fetchone()

    if not row:
        return None

    return {
        "id": row[0],
        "tenant_id": row[1],
        "review_id": row[2],
        "model": row[3],
        "sentiment": row[4],
        "sentiment_score": row[5],
        "key_points": json.loads(row[6]) if row[6] else [],
        "topics": json.loads(row[7]) if row[7] else [],
        "suggestions": json.loads(row[8]) if row[8] else [],
        "summary": row[9],
        "raw_response": row[10]
    }


def analyze_and_save_single_review(db: Session, review_data: Dict[str, Any]) -> Optional[dict]:
    """分析单条评论并保存到数据库（用于聊天时自动保存）"""
    review_id = review_data["id"]
    product_name = review_data.get("product_name", "未知商品")

    existing_analysis = get_review_analysis(db, review_id)
    if existing_analysis:
        logger.info(f"评论{review_id}({product_name})已有分析结果，跳过")
        return existing_analysis

    if not client:
        logger.warning(f"OpenAI API未配置，跳过评论{review_id}分析")
        return None

    try:
        content = review_data["content"]
        title = review_data.get("title", "") or ""
        translated_content = review_data.get("translated_content", "")
        translated_title = review_data.get("translated_title", "")
        
        # 如果没有翻译，先进行翻译并保存到数据库
        if not translated_content:
            logger.debug(f"评论{review_id}({product_name})未翻译，正在翻译...")
            try:
                from services.translate_service import translate_review
                translated_title, translated_content = translate_review(title, content)
                
                # 保存翻译到数据库
                update_query = text("""
                    UPDATE reviews 
                    SET translated_title = :translated_title, translated_content = :translated_content
                    WHERE id = :review_id
                """)
                db.execute(update_query, {
                    "translated_title": translated_title,
                    "translated_content": translated_content,
                    "review_id": review_id
                })
                db.commit()
                logger.info(f"[OK] 评论{review_id}({product_name})翻译已保存")
            except Exception as e:
                logger.error(f"翻译失败: {e}")

        prompt = f"""请分析以下差评并提供详细分析：

【商品名称】：{product_name}
【评分】: {review_data['rating']}星
【标题】: {review_data.get('title', '') or '无'}
【原文内容】: {content}
【中文翻译】: {translated_content or '无'}

重要性分级规则：
1. high（最高级）：货不对板、颜色不对、产品不是同一种、规格不符
2. medium（第二级）：质量不好、破损、少件、缺配件、损坏
3. low（第三级）：其他所有场景

请严格按照以下JSON格式输出（不要输出其他内容）：
{{
    "sentiment": "negative|neutral|positive",
    "sentiment_score": 1-10,
    "key_points": ["要点1", "要点2"],
    "topics": ["主题1", "主题2"],
    "suggestions": ["建议1", "建议2"],
    "summary": "一句话总结",
    "importance_level": "high|medium|low"
}}
"""

        response = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "你是专业的跨境电商差评分析师。所有分析结果必须使用中文输出。只输出JSON，不要输出其他内容。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3
        )

        response_content = response.choices[0].message.content.strip()
        
        # 清理可能的markdown标记
        if response_content.startswith("```"):
            response_content = response_content.split("\n", 1)[-1]
            if response_content.endswith("```"):
                response_content = response_content[:-3]
            response_content = response_content.strip()

        try:
            ai_result = json.loads(response_content)
        except json.JSONDecodeError:
            ai_result = {
                "sentiment": "negative",
                "sentiment_score": 3,
                "key_points": ["分析失败"],
                "topics": ["未知"],
                "suggestions": ["人工查看"],
                "summary": response_content[:200]
            }

        insert_query = text("""
            INSERT INTO review_analyses (
                tenant_id, review_id, model, sentiment, sentiment_score,
                key_points, topics, suggestions, summary, raw_response
            ) VALUES (
                :tenant_id, :review_id, :model, :sentiment, :sentiment_score,
                :key_points, :topics, :suggestions, :summary, :raw_response
            )
        """)

        db.execute(insert_query, {
            "tenant_id": review_data.get("tenant_id", 1),
            "review_id": review_id,
            "model": settings.OPENAI_MODEL,
            "sentiment": ai_result.get("sentiment", "negative"),
            "sentiment_score": ai_result.get("sentiment_score", 3),
            "key_points": json.dumps(ai_result.get("key_points", [])),
            "topics": json.dumps(ai_result.get("topics", [])),
            "suggestions": json.dumps(ai_result.get("suggestions", [])),
            "summary": ai_result.get("summary", ""),
            "raw_response": response_content
        })
        
        # 更新重要性等级
        importance_level = ai_result.get("importance_level", "low")
        if importance_level not in ["high", "medium", "low"]:
            importance_level = "low"
        
        # 先检查importance_level列是否存在
        try:
            col_check = db.execute(text("SHOW COLUMNS FROM reviews LIKE 'importance_level'"))
            if col_check.fetchone():
                result = db.execute(text("""
                    UPDATE reviews SET importance_level = :level WHERE id = :rid
                """), {"level": importance_level, "rid": review_id})
                logger.info(f"[OK] 评论{review_id}重要性等级: {importance_level}, 影响行数: {result.rowcount}")
                db.commit()  # 立即提交
        except Exception as e:
            logger.error(f"更新重要性等级失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
        
        # 再提交一次确保所有内容都保存
        db.commit()

        logger.info(f"[OK] 评论{review_id}({product_name})分析已保存到review_analyses表")
        return get_review_analysis(db, review_id)

    except Exception as e:
        logger.error(f"分析评论{review_id}失败: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return None


def analyze_review(db: Session, review: Review) -> dict:
    """使用AI分析评论并保存结果"""
    if not client:
        raise Exception("OpenAI API Key 未配置")

    try:
        existing_analysis = get_review_analysis(db, review.id)
        if existing_analysis:
            return existing_analysis

        prompt = f"""请分析以下差评：

评分: {review.rating}
标题: {review.title or '无'}
内容: {review.content}
翻译: {review.translated_content or '无'}

重要性分级规则：
1. high（最高级）：货不对板、颜色不对、产品不是同一种、规格不符
2. medium（第二级）：质量不好、破损、少件、缺配件、损坏
3. low（第三级）：其他所有场景

输出JSON格式：
{{"sentiment":"negative","sentiment_score":3,"key_points":[],"topics":[],"suggestions":[],"summary":"","importance_level":"high|medium|low"}}
"""

        response = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "你是专业差评分析助手。所有分析结果必须使用中文输出。只输出JSON。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3
        )

        response_content = response.choices[0].message.content.strip()
        
        if response_content.startswith("```"):
            response_content = response_content.split("\n", 1)[-1]
            if response_content.endswith("```"):
                response_content = response_content[:-3]
            response_content = response_content.strip()

        try:
            result = json.loads(response_content)
        except json.JSONDecodeError:
            result = {"sentiment": "negative", "sentiment_score": 3, "key_points": [], "topics": [], "suggestions": [], "summary": response_content}

        insert_query = text("""
            INSERT INTO review_analyses (tenant_id, review_id, model, sentiment, sentiment_score, key_points, topics, suggestions, summary, raw_response)
            VALUES (:tenant_id, :review_id, :model, :sentiment, :score, :kp, :top, :sug, :sum, :raw)
        """)
        db.execute(insert_query, {
            "tenant_id": review.tenant_id, "review_id": review.id, "model": settings.OPENAI_MODEL,
            "sentiment": result.get("sentiment", "negative"), "score": result.get("sentiment_score", 3),
            "kp": json.dumps(result.get("key_points", [])), "top": json.dumps(result.get("topics", [])),
            "sug": json.dumps(result.get("suggestions", [])), "sum": result.get("summary", ""), "raw": response_content
        })
        
        # 更新重要性等级
        importance_level = result.get("importance_level", "low")
        if importance_level not in ["high", "medium", "low"]:
            importance_level = "low"
        
        # 先检查importance_level列是否存在
        try:
            col_check = db.execute(text("SHOW COLUMNS FROM reviews LIKE 'importance_level'"))
            if col_check.fetchone():
                update_result = db.execute(text("""
                    UPDATE reviews SET importance_level = :level WHERE id = :rid
                """), {"level": importance_level, "rid": review.id})
                logger.info(f"评论{review.id}重要性等级: {importance_level}, 影响行数: {update_result.rowcount}")
                db.commit()  # 立即提交
        except Exception as e:
            logger.error(f"更新重要性等级失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
        
        # 再提交一次确保所有内容都保存
        db.commit()

        return get_review_analysis(db, review.id)

    except Exception as e:
        logger.error(f"分析评论失败: {str(e)}")
        raise


def batch_analyze_reviews(db: Session, review_ids: List[int]) -> List[Dict[str, Any]]:
    """批量分析评论"""
    results = []
    import time

    for idx, review_id in enumerate(review_ids):
        try:
            logger.info(f"正在分析第 {idx+1}/{len(review_ids)} 条评论，ID: {review_id}")
            
            check_query = text("SELECT id, tenant_id, title, content, translated_title, translated_content, rating FROM reviews WHERE id = :rid")
            check_result = db.execute(check_query, {"rid": review_id}).first()
            if not check_result:
                results.append({"review_id": review_id, "success": False, "message": "不存在"})
                continue

            analysis = get_review_analysis(db, review_id)

            if analysis:
                results.append({"review_id": review_id, "success": True, "data": analysis})
            else:
                tenant_id = check_result[1]
                title = check_result[2] or ""
                content = check_result[3] or ""
                translated_title = check_result[4] or ""
                translated_content = check_result[5] or ""

                if not translated_content:
                    try:
                        from services.translate_service import translate_review
                        tt, tc = translate_review(title, content)
                        db.execute(text("UPDATE reviews SET translated_title=:tt, translated_content=:tc WHERE id=:rid"), {"tt": tt, "tc": tc, "rid": review_id})
                        db.commit()
                        translated_content = tc
                    except Exception as e:
                        logger.error(f"翻译失败: {e}")

                prompt = f"""分析差评：评分{check_result[6]}星，标题:{title or '无'}，内容:{content}，翻译:{translated_content or '无'}

重要性分级规则：
1. high（最高级）：货不对板、颜色不对、产品不是同一种、规格不符
2. medium（第二级）：质量不好、破损、少件、缺配件、损坏
3. low（第三级）：其他所有场景

输出JSON:{{"sentiment":"","sentiment_score":0,"key_points":[],"topics":[],"suggestions":[],"summary":"","importance_level":"high|medium|low"}}"""

                if client:
                    try:
                        resp = client.chat.completions.create(model=settings.OPENAI_MODEL, messages=[{"role":"system","content":"你是专业的跨境电商差评分析师。所有分析结果必须使用中文输出。只输出JSON，不要输出其他内容。"},{"role":"user","content":prompt}], temperature=0.3, timeout=120)
                        rc = resp.choices[0].message.content.strip()
                        if rc.startswith("```"): rc = rc.split("\n",1)[-1]
                        if rc.endswith("```"): rc = rc[:-3]
                        rc = rc.strip()
                        ar = json.loads(rc) if rc.startswith("{") else {}
                        
                        db.execute(text("""INSERT INTO review_analyses (tenant_id,review_id,model,sentiment,sentiment_score,key_points,topics,suggestions,summary,raw_response) VALUES (:tid,:rid,:m,:s,:sc,:kp,:t,:sg,:sm,:r)"""), {
                            "tid": tenant_id, "rid": review_id, "m": settings.OPENAI_MODEL, "s": ar.get("sentiment","negative"), "sc": ar.get("sentiment_score",3), "kp": json.dumps(ar.get("key_points",[])), "t": json.dumps(ar.get("topics",[])), "sg": json.dumps(ar.get("suggestions",[])), "sm": ar.get("summary",""), "r": rc
                        })
                        db.commit()
                        
                        # 更新重要性等级
                        importance_level = ar.get("importance_level", "low")
                        if importance_level not in ["high", "medium", "low"]:
                            importance_level = "low"
                        
                        try:
                            col_check = db.execute(text("SHOW COLUMNS FROM reviews LIKE 'importance_level'"))
                            if col_check.fetchone():
                                update_result = db.execute(text("""
                                    UPDATE reviews SET importance_level = :level WHERE id = :rid
                                """), {"level": importance_level, "rid": review_id})
                                logger.info(f"评论{review_id}重要性等级: {importance_level}, 影响行数: {update_result.rowcount}")
                                db.commit()  # 立即提交
                        except Exception as e:
                            logger.error(f"更新重要性等级失败: {e}")
                            import traceback
                            logger.error(traceback.format_exc())
                        
                        results.append({"review_id": review_id, "success": True, "data": get_review_analysis(db, review_id)})
                    except Exception as ex:
                        logger.error(f"分析评论 {review_id} 时发生错误: {ex}")
                        results.append({"review_id": review_id, "success": False, "data": {"error": str(ex)}})
                else:
                    results.append({"review_id": review_id, "success": True, "data": {"error": "API未配置"}})
        except Exception as e:
            results.append({"review_id": review_id, "success": False, "message": str(e)})

    return results


def save_message(db: Session, user_id: int, session_id: str, role: str, content: str, function_name: Optional[str] = None, chat_type: str = "review"):
    message = ConversationHistory(user_id=user_id, session_id=session_id, role=role, content=content, function_name=function_name, chat_type=chat_type)
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def get_conversation_history(db: Session, user_id: int, session_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    messages = db.query(ConversationHistory).filter(ConversationHistory.user_id == user_id, ConversationHistory.session_id == session_id).order_by(ConversationHistory.created_at).limit(limit).all()
    return [{"role": m.role, "content": m.content} for m in messages if m.role in ["system", "user", "assistant"]]


def process_chat(db: Session, user_id: int, session_id: str, user_message: str, chat_type: str = "review") -> str:
    """处理聊天请求 - chat_type: review=差评分析, inventory=库存分析"""
    logger.debug(f"[CHAT] 收到消息, 用户={user_id}, 类型={chat_type}")

    if not client:
        return "OpenAI API Key 未配置"

    save_message(db, user_id, session_id, "user", user_message, chat_type=chat_type)
    history = get_conversation_history(db, user_id, session_id, limit=10)
    current_date = datetime.now().strftime("%Y-%m-%d")

    # 根据对话类型选择不同的提示词和工具
    if chat_type == "inventory":
        system_prompt = f"""你是专业的跨境电商库存分析助手。当前日期: {current_date}。

你的能力：
- 查询库存状态、断货风险商品、补货建议
- 分析库存健康度，给出补货优先级建议

重要规则：
- 使用商品的【真实名称】来引用产品，不要只使用ASIN
- 回复要简洁专业，突出关键数据和建议
- 优先展示风险等级和建议补货数量
- 对于库存数据，用表格或列表形式清晰展示
"""
        tools = INVENTORY_TOOLS
    else:
        system_prompt = f"""你是专业的跨境电商差评分析助手。当前日期: {current_date}。

任务：
1. 解析用户提到的日期范围
2. 查询该日期范围内的差评
3. 用中文进行分析回复

重要规则：
- 绝对不要在回复中使用数字ID或ASIN编号来标识产品
- 必须使用商品的【真实名称】来引用产品
- 引用格式：【商品名】具体问题描述
"""
        tools = DATE_PARSING_TOOLS

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    try:
        response = client.chat.completions.create(model=settings.OPENAI_MODEL, messages=messages, tools=tools, tool_choice="auto", timeout=180)

        assistant_message = response.choices[0].message

        if assistant_message.tool_calls:
            for tool_call in assistant_message.tool_calls:
                # 处理库存查询
                if tool_call.function.name == "query_inventory_status":
                    args = json.loads(tool_call.function.arguments)
                    query_type = args.get("query_type", "all")
                    risk_level = args.get("risk_level")
                    limit = args.get("limit", 10)

                    logger.info(f"[CHAT] 库存查询: type={query_type}, risk={risk_level}, limit={limit}")
                    inventory_items = query_inventory_status(db, query_type, risk_level, limit)
                    logger.info(f"[CHAT] 查询到 {len(inventory_items)} 条库存数据")

                    # 构建给AI的数据
                    inventory_prompt = f"""当前日期: {current_date}

查询到 {len(inventory_items)} 条库存数据：

{json.dumps(inventory_items, ensure_ascii=False, indent=1)}

请基于以上数据进行专业的库存分析，给出：
1. 整体库存状况概述
2. 重点关注商品列表（按风险等级排序）
3. 补货建议

回复要简洁专业，使用商品名称而非ASIN。"""

                    final_messages = [{"role": "system", "content": inventory_prompt}]
                    final_messages.extend(history)
                    final_messages.append({"role": "user", "content": user_message})

                    final_response = client.chat.completions.create(model=settings.OPENAI_MODEL, messages=final_messages, temperature=0.7, timeout=240)
                    final_reply = final_response.choices[0].message.content or "抱歉，无法处理"

                    save_message(db, user_id, session_id, "assistant", final_reply, chat_type=chat_type)
                    return final_reply

                # 处理日期解析（差评查询）
                if tool_call.function.name == "parse_date_range":
                    args = json.loads(tool_call.function.arguments)
                    # 添加健壮性检查
                    start_date = args.get("start_date")
                    end_date = args.get("end_date")

                    # 如果AI没有正确返回日期，重新调用AI要求明确日期
                    if not start_date or not end_date:
                        logger.warning(f"[CHAT] AI未正确返回日期，重新要求明确日期")
                        # 保存当前消息
                        save_message(db, user_id, session_id, "assistant", assistant_message.content or "", chat_type=chat_type)
                        # 重新发送明确要求
                        clarify_prompt = """请务必调用parse_date_range工具，并明确返回：
- start_date: YYYY-MM-DD格式的开始日期
- end_date: YYYY-MM-DD格式的结束日期
- date_description: 日期描述

请确保正确调用工具，不要用自然语言回复。"""
                        
                        messages.append({"role": "assistant", "content": assistant_message.content or ""})
                        messages.append({"role": "user", "content": clarify_prompt})
                        
                        # 重新调用AI
                        response = client.chat.completions.create(model=settings.OPENAI_MODEL, messages=messages, tools=DATE_PARSING_TOOLS, tool_choice="auto", timeout=180)
                        assistant_message = response.choices[0].message
                        
                        # 检查第二次调用是否有工具响应
                        if assistant_message.tool_calls:
                            for tool_call_2 in assistant_message.tool_calls:
                                if tool_call_2.function.name == "parse_date_range":
                                    args_2 = json.loads(tool_call_2.function.arguments)
                                    start_date = args_2.get("start_date")
                                    end_date = args_2.get("end_date")
                                    break
                    
                    # 如果第二次调用还是没有日期，使用默认日期
                    if not start_date or not end_date:
                        logger.warning(f"[CHAT] AI仍然未正确返回日期，使用默认日期")
                        end_date = datetime.now().strftime("%Y-%m-%d")
                        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
                    
                    logger.info(f"[CHAT] 查询日期: {start_date} ~ {end_date}")
                    reviews = query_negative_reviews(db, start_date, end_date)
                    logger.info(f"[CHAT] 查询到 {len(reviews)} 条差评")

                    # 构建给AI的数据 - 只包含商品名，不含ID（关键！）
                    reviews_for_ai = []
                    analyzed_count = 0
                    
                    for review in reviews:
                        product_name = review.get("product_name", review["asin"])
                        logger.debug(f"[DB] 评论ID={review['id']} -> 产品名={product_name}")
                        
                        # 自动分析和保存（注意：这里仍然要用review_id，但只在内部使用）
                        analysis = get_review_analysis(db, review["id"])
                        if not analysis:
                            logger.debug(f"[CHAT] 自动分析评论: {product_name}")
                            analysis = analyze_and_save_single_review(db, review)
                            if analysis:
                                analyzed_count += 1
                        
                        # ⚠️ 关键：构建给AI的数据时，完全不包含ID字段！
                        ai_data_item = {
                            "product_name": product_name,  # ✅ 只用商品名
                            "rating": review["rating"],
                            "title": review.get("title", "") or "",
                            "content_preview": review["content"][:150] + ("..." if len(review["content"]) > 150 else ""),
                            "translation_preview": (review.get("translated_content") or "")[:150],
                            "key_issues": analysis["key_points"] if analysis else [],
                            "summary": analysis["summary"] if analysis else ""
                        }
                        
                        reviews_for_ai.append(ai_data_item)

                    logger.info(f"[CHAT] 新分析了 {analyzed_count} 条评论并保存到数据库")
                    logger.debug(f"[AI] 准备发送的数据样例: {json.dumps(reviews_for_ai[:2], ensure_ascii=False)}")

                    # 构建更严格的提示词
                    analysis_prompt = f"""当前日期: {current_date}

你有以下差评数据（共{len(reviews_for_ai)}条）：

{json.dumps(reviews_for_ai, ensure_ascii=False, indent=1)}

【严格规则 - 违反将扣分】：
1. 回复中必须使用"商品名称"字段来指代产品
2. 禁止使用任何数字ID、ASIN编号
3. 正确示例："【Party Bags】质量差，塑料感重"
4. 错误示例："562号产品质量差" 或 "B0XXX质量差"

请基于以上数据进行专业的差评分析，给出改进建议。
"""

                    final_messages = [{"role": "system", "content": analysis_prompt}]
                    final_messages.extend(history)
                    final_messages.append({"role": "user", "content": user_message})

                    final_response = client.chat.completions.create(model=settings.OPENAI_MODEL, messages=final_messages, temperature=0.7, timeout=240)
                    final_reply = final_response.choices[0].message.content or "抱歉，无法处理"
                    
                    save_message(db, user_id, session_id, "assistant", final_reply, chat_type=chat_type)
                    return final_reply
        else:
            reply = assistant_message.content or "请说明想查看的日期范围"
            save_message(db, user_id, session_id, "assistant", reply, chat_type=chat_type)
            return reply

    except Exception as e:
        logger.error(f"[CHAT] 错误: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return f"处理出错: {str(e)}"


def create_session_id() -> str:
    return str(uuid.uuid4())

