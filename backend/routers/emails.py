from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from database.database import get_db
from dependencies import get_current_user
from models.user import User
from pydantic import BaseModel, Field
from typing import Optional
import logging
import httpx
from openai import OpenAI
from config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

try:
    ai_client = OpenAI(
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_API_BASE
    ) if settings.OPENAI_API_KEY else None
except Exception:
    ai_client = None

router = APIRouter(prefix="/emails", tags=["emails"])

FEISHU_WEBHOOK_URL = "https://pcn4p6l5do51.feishu.cn/base/automation/webhook/event/ScaZalvInwme5rhCQ0PckNA1nfx"

SITE_NAME_MAP = {
    "amazon_us": "美国", "amazon_uk": "英国", "amazon_de": "德国",
    "amazon_jp": "日本", "amazon_au": "澳洲", "amazon_fr": "法国",
    "amazon_it": "意大利", "amazon_es": "西班牙", "amazon_ca": "加拿大",
    "amazon_in": "印度", "amazon_mx": "墨西哥", "amazon_br": "巴西",
    "amazon_nl": "荷兰", "amazon_se": "瑞典", "amazon_pl": "波兰",
    "amazon_be": "比利时", "amazon_tr": "土耳其", "amazon_ae": "阿联酋",
    "amazon_sa": "沙特", "amazon_sg": "新加坡", "amazon_eg": "埃及",
}

LANGUAGE_NAME_MAP = {
    "en": "英语", "de": "德语", "fr": "法语", "ja": "日语",
    "it": "意大利语", "es": "西班牙语", "pt": "葡萄牙语",
    "nl": "荷兰语", "sv": "瑞典语", "pl": "波兰语",
    "tr": "土耳其语", "ar": "阿拉伯语", "zh": "中文",
    "ko": "韩语", "hi": "印地语", "ru": "俄语",
}

def get_site_name(site: str) -> str:
    return SITE_NAME_MAP.get(site, site)

def get_language_name(lang: str) -> str:
    return LANGUAGE_NAME_MAP.get(lang, lang)

def get_importance_level(mail_subject: str) -> str:
    if not mail_subject:
        return "normal"
    urgent_keywords = ['修改订单', '发票请求', '取消订单']
    for kw in urgent_keywords:
        if kw in mail_subject:
            return "urgent"
    medium_keywords = ['退货和换货', '配送和追踪', '商品定制']
    for kw in medium_keywords:
        if kw in mail_subject:
            return "medium"
    return "normal"

IMPORTANCE_LABEL_MAP = {
    "urgent": "紧急",
    "medium": "中等",
    "normal": "一般",
}

class UpdateFollowUpRequest(BaseModel):
    """更新跟进状态请求"""
    follow_up_status: int = Field(..., description="跟进状态：0=未跟进，1=已跟进")


class BatchUpdateFollowUpRequest(BaseModel):
    """批量更新跟进状态请求"""
    email_ids: list = Field(..., min_length=1, description="邮件ID列表")
    follow_up_status: int = Field(..., description="跟进状态：0=未跟进，1=已跟进")

class UpdateNeedReplyRequest(BaseModel):
    """更新需要回复状态请求"""
    need_reply: int = Field(..., description="需要回复：0=不需要，1=需要")
    reply_text: Optional[str] = Field(None, description="回复内容")


class AIReplyRequest(BaseModel):
    """AI生成回复请求"""
    requirements: str = Field(..., min_length=1, description="自定义回复需求")


@router.get("/")
async def get_email_messages(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    buyer_mail_number_search: str = Query(None, description="买家邮件号搜索"),
    store_name_search: str = Query(None, description="账号名称搜索"),
    follow_up_status: int = Query(None, description="跟进状态筛选：0-未跟进，1-已跟进"),
    mail_subject: str = Query(None, description="邮件主题筛选"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取邮件信息列表（支持分页、搜索）"""
    try:
        logger.info(f"用户 {current_user.username} (ID:{current_user.id}) 请求获取邮件列表")
        
        where_conditions = []
        params = {"limit": page_size, "offset": (page - 1) * page_size}

        if current_user.role != "admin":
            params["user_id"] = current_user.id
            where_conditions.append("""
                s.id IN (
                    SELECT s2.id FROM stores s2
                    WHERE s2.department_id IN (
                        SELECT ud.department_id FROM user_departments ud
                        WHERE ud.user_id = :user_id
                    )
                )
            """)

        if buyer_mail_number_search:
            where_conditions.append("e.buyer_mail_number LIKE :buyer_mail_number_search")
            params["buyer_mail_number_search"] = f"%{buyer_mail_number_search}%"

        if store_name_search:
            where_conditions.append("s.name LIKE :store_name_search")
            params["store_name_search"] = f"%{store_name_search}%"

        if follow_up_status is not None:
            where_conditions.append("e.follow_up_status = :follow_up_status")
            params["follow_up_status"] = follow_up_status

        if mail_subject:
            where_conditions.append("e.mail_subject LIKE :mail_subject")
            params["mail_subject"] = f"%{mail_subject}%"

        where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"

        order_by_clause = "e.reply_date DESC"

        count_query = text(f"""
            SELECT COUNT(*)
            FROM email_messages e
            LEFT JOIN stores s ON e.store_id = s.id
            WHERE {where_clause}
        """)
        count_result = db.execute(count_query, params)
        total = count_result.scalar()
        
        logger.info(f"找到 {total} 条邮件记录")

        query = text(f"""
            SELECT
                e.id,
                e.tenant_id,
                e.store_id,
                e.site,
                e.language,
                e.mail_subject,
                e.mail_content,
                e.mail_content_chinese,
                e.buyer_mail_number,
                e.ai_reply_content,
                e.reply_date,
                s.name AS store_name,
                e.follow_up_status,
                e.need_reply,
                e.reply_text,
                e.reply_text_time
            FROM email_messages e
            LEFT JOIN stores s ON e.store_id = s.id
            WHERE {where_clause}
            ORDER BY {order_by_clause}
            LIMIT :limit OFFSET :offset
        """)

        result = db.execute(query, params)
        emails = result.fetchall()

        email_data = []
        for row in emails:
            email_data.append({
                "id": str(row[0]),
                "tenant_id": row[1] or "",
                "store_id": row[2] or "",
                "site": row[3] or "",
                "language": row[4] or "",
                "mail_subject": row[5] or "",
                "mail_content": row[6] or "",
                "mail_content_chinese": row[7] or "",
                "buyer_mail_number": row[8] or "",
                "ai_reply_content": row[9] or "",
                "reply_date": row[10].strftime("%Y-%m-%d %H:%M:%S") if row[10] is not None else "",
                "store_name": row[11] or "",
                "follow_up_status": row[12] or 0,
                "need_reply": row[13] or 0,
                "reply_text": row[14] or "",
                "reply_text_time": row[15].strftime("%Y-%m-%d %H:%M:%S") if row[15] is not None else "",
                "importance_level": get_importance_level(row[5] or ""),
            })
        
        logger.info(f"返回 {len(email_data)} 条邮件记录")
        
        return {
            "success": True,
            "data": email_data,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size if total > 0 else 0
        }

    except Exception as e:
        logger.error(f"获取邮件列表失败: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取邮件列表失败: {str(e)}")


@router.get("/store-names")
async def get_store_names(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取所有店铺名称（去重），用于下拉选择"""
    try:
        if current_user.role != "admin":
            query = text("""
                SELECT DISTINCT s.name FROM stores s
                WHERE s.department_id IN (
                    SELECT ud.department_id FROM user_departments ud
                    WHERE ud.user_id = :user_id
                )
                ORDER BY s.name
            """)
            result = db.execute(query, {"user_id": current_user.id})
        else:
            query = text("SELECT DISTINCT name FROM stores ORDER BY name")
            result = db.execute(query)

        names = [row[0] for row in result.fetchall() if row[0]]
        return {"success": True, "data": names}
    except Exception as e:
        logger.error(f"获取店铺名称列表失败: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取店铺名称列表失败: {str(e)}")


@router.get("/unfollowed-count")
async def get_unfollowed_count(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取未跟进邮件数量（按重要程度分类）"""
    try:
        dep_filter = ""
        dep_params = {}
        if current_user.role != "admin":
            dep_filter = """ AND e.store_id IN (
                SELECT s2.id FROM stores s2
                WHERE s2.department_id IN (
                    SELECT ud.department_id FROM user_departments ud
                    WHERE ud.user_id = :user_id
                )
            )"""
            dep_params["user_id"] = current_user.id

        query = text(f"""
            SELECT
                SUM(CASE WHEN e.mail_subject LIKE '%修改订单%'
                         OR e.mail_subject LIKE '%发票请求%'
                         OR e.mail_subject LIKE '%取消订单%' THEN 1 ELSE 0 END) as urgent,
                SUM(CASE WHEN e.mail_subject LIKE '%退货和换货%'
                         OR e.mail_subject LIKE '%配送和追踪%'
                         OR e.mail_subject LIKE '%商品定制%' THEN 1 ELSE 0 END) as medium,
                SUM(CASE WHEN e.mail_subject NOT LIKE '%修改订单%'
                         AND e.mail_subject NOT LIKE '%发票请求%'
                         AND e.mail_subject NOT LIKE '%取消订单%'
                         AND e.mail_subject NOT LIKE '%退货和换货%'
                         AND e.mail_subject NOT LIKE '%配送和追踪%'
                         AND e.mail_subject NOT LIKE '%商品定制%' THEN 1 ELSE 0 END) as normal,
                COUNT(*) as total
            FROM email_messages e
            WHERE e.follow_up_status = 0{dep_filter}
        """)

        result = db.execute(query, dep_params)
        row = result.fetchone()

        return {
            "success": True,
            "data": {
                "urgent": row[0] or 0,
                "medium": row[1] or 0,
                "normal": row[2] or 0,
                "total": row[3] or 0,
            }
        }
    except Exception as e:
        logger.error(f"获取未跟进邮件数量失败: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取未跟进邮件数量失败: {str(e)}")


@router.post("/{email_id}/ai-reply")
async def generate_ai_reply(
    email_id: str,
    request: AIReplyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """AI生成邮件回复"""
    if not ai_client:
        raise HTTPException(status_code=503, detail="AI服务未配置")

    try:
        query = text("""
            SELECT e.mail_subject, e.mail_content, e.mail_content_chinese,
                   e.language, e.site, e.buyer_mail_number
            FROM email_messages e
            LEFT JOIN stores s ON e.store_id = s.id
            WHERE e.id = :email_id
        """)
        result = db.execute(query, {"email_id": email_id})
        row = result.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail=f"邮件 {email_id} 不存在")

        mail_subject = row[0] or ""
        mail_content = row[1] or ""
        mail_content_chinese = row[2] or ""
        language = row[3] or "en"
        site_name = get_site_name(row[4] or "")
        buyer_email = row[5] or ""

        language_name = get_language_name(language)

        chinese_ref = f"\n\n邮件中文翻译参考：\n{mail_content_chinese}" if mail_content_chinese else ""
        site_info = f"站点：{site_name}" if site_name else ""
        
        system_prompt = f"""你是专业的跨境电商客服人员。请根据以下邮件内容生成专业、礼貌的回复。

要求：
1. 回复语言使用{language_name}撰写
2. 保持专业、友好的语气
3. 以下是用户的自定义需求，请严格按要求回复：
{request.requirements}

请输出纯文本格式的回复内容，不要添加任何说明。"""

        user_prompt = f"""原始邮件主题：{mail_subject}
原始邮件内容：
{mail_content}{chinese_ref}
{site_info}
买家邮箱：{buyer_email}"""

        logger.info(f"AI生成回复: 邮件ID={email_id}, 需求={request.requirements[:100]}")

        response = ai_client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.7,
            timeout=120
        )

        reply_text = response.choices[0].message.content.strip() if response.choices else ""

        translate_response = ai_client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "你是一个专业翻译助手，请将以下文本翻译成中文，只输出翻译结果。"},
                {"role": "user", "content": reply_text}
            ],
            temperature=0.3,
            timeout=60
        )

        reply_text_chinese = translate_response.choices[0].message.content.strip() if translate_response.choices else ""

        logger.info(f"AI回复生成成功: 邮件ID={email_id}, 回复长度={len(reply_text)}")

        return {
            "success": True,
            "data": {
                "reply_text": reply_text,
                "reply_text_chinese": reply_text_chinese
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"AI生成回复失败: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"AI生成回复失败: {str(e)}")


@router.get("/department-todos")
async def get_department_todos(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取当前用户的待办事项（按机器人类型汇总）"""
    try:
        if current_user.role == "admin":
            dep_filter_review = ""
            dep_filter_email = ""
        else:
            dep_filter_review = """ AND s.department_id IN (
                SELECT ud.department_id FROM user_departments ud WHERE ud.user_id = :user_id
            )"""
            dep_filter_email = dep_filter_review

        params = {}
        if current_user.role != "admin":
            params["user_id"] = current_user.id

        review_query = text(f"""
            SELECT COUNT(*), MAX(r.review_date)
            FROM reviews r
            JOIN stores s ON r.store_id = s.id
            WHERE r.status NOT IN ('resolved', 'dismissed')
            AND r.tenant_id = (SELECT tenant_id FROM users WHERE id = :user_id)
            AND r.deleted_at IS NULL{dep_filter_review}
        """)
        result = db.execute(review_query, params if params else {"user_id": current_user.id})
        review_row = result.fetchone()
        pending_reviews = review_row[0] or 0
        latest_review_date = review_row[1].strftime('%Y-%m-%d') if review_row[1] else ''

        email_query = text(f"""
            SELECT COUNT(*), MAX(e.reply_date)
            FROM email_messages e
            JOIN stores s ON e.store_id = s.id
            WHERE e.follow_up_status = 0{dep_filter_email}
        """)
        result = db.execute(email_query, params)
        email_row = result.fetchone()
        pending_emails = email_row[0] or 0
        latest_email_date = email_row[1].strftime('%Y-%m-%d') if email_row[1] else ''

        return {
            "success": True,
            "data": {
                "reviews": {"count": pending_reviews, "latest_date": latest_review_date},
                "emails": {"count": pending_emails, "latest_date": latest_email_date},
            }
        }

    except Exception as e:
        logger.error(f"获取待办事项失败: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取待办事项失败: {str(e)}")


@router.get("/{email_id}")
async def get_email_detail(
    email_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取邮件详情"""
    try:
        if current_user.role != "admin":
            access_filter = """ AND e.store_id IN (
                SELECT s2.id FROM stores s2
                WHERE s2.department_id IN (
                    SELECT ud.department_id FROM user_departments ud
                    WHERE ud.user_id = :user_id
                )
            )"""
        else:
            access_filter = ""

        query = text(f"""
            SELECT
                e.id,
                e.tenant_id,
                e.store_id,
                e.site,
                e.language,
                e.mail_subject,
                e.mail_content,
                e.mail_content_chinese,
                e.buyer_mail_number,
                e.ai_reply_content,
                e.reply_date,
                s.name AS store_name,
                e.follow_up_status,
                e.need_reply,
                e.reply_text,
                e.reply_text_time
            FROM email_messages e
            LEFT JOIN stores s ON e.store_id = s.id
            WHERE e.id = :email_id{access_filter}
        """)

        params = {"email_id": email_id}
        if current_user.role != "admin":
            params["user_id"] = current_user.id

        result = db.execute(query, params)
        row = result.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail=f"邮件 {email_id} 不存在")

        email_detail = {
            "id": str(row[0]),
            "tenant_id": row[1] or "",
            "store_id": row[2] or "",
            "site": row[3] or "",
            "language": row[4] or "",
            "mail_subject": row[5] or "",
            "mail_content": row[6] or "",
            "mail_content_chinese": row[7] or "",
            "buyer_mail_number": row[8] or "",
            "ai_reply_content": row[9] or "",
            "reply_date": row[10].strftime("%Y-%m-%d %H:%M:%S") if row[10] else "",
            "store_name": row[11] or "",
            "follow_up_status": row[12] or 0,
            "need_reply": row[13] or 0,
            "reply_text": row[14] or "",
            "reply_text_time": row[15].strftime("%Y-%m-%d %H:%M:%S") if row[15] is not None else "",
            "importance_level": get_importance_level(row[5] or ""),
        }

        return {"success": True, "data": email_detail}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取邮件详情失败: {str(e)}")


@router.put("/batch/follow-up")
async def batch_update_follow_up(
    request: BatchUpdateFollowUpRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """批量更新邮件跟进状态"""
    try:
        if not request.email_ids:
            raise HTTPException(status_code=400, detail="邮件ID列表不能为空")

        if current_user.role != "admin":
            access_filter = """ AND store_id IN (
                SELECT s2.id FROM stores s2
                WHERE s2.department_id IN (
                    SELECT ud.department_id FROM user_departments ud
                    WHERE ud.user_id = :user_id
                )
            )"""
        else:
            access_filter = ""

        placeholders = ','.join([f":id_{i}" for i in range(len(request.email_ids))])
        update_query = text(f"""
            UPDATE email_messages 
            SET follow_up_status = :follow_up_status
            WHERE id IN ({placeholders}){access_filter}
        """)

        params = {"follow_up_status": request.follow_up_status}
        for i, email_id in enumerate(request.email_ids):
            params[f"id_{i}"] = email_id
        if current_user.role != "admin":
            params["user_id"] = current_user.id

        result = db.execute(update_query, params)
        db.commit()

        logger.info(f"用户 {current_user.username} 批量更新了 {result.rowcount} 封邮件的跟进状态为 {request.follow_up_status}")

        return {"success": True, "message": f"成功更新 {result.rowcount} 封邮件", "count": result.rowcount}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"批量更新跟进状态失败: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"批量更新跟进状态失败: {str(e)}")


@router.put("/{email_id}/follow-up")
async def update_follow_up(
    email_id: str,
    request: UpdateFollowUpRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """更新邮件跟进状态"""
    try:
        if current_user.role != "admin":
            access_filter = """ AND store_id IN (
                SELECT s2.id FROM stores s2
                WHERE s2.department_id IN (
                    SELECT ud.department_id FROM user_departments ud
                    WHERE ud.user_id = :user_id
                )
            )"""
        else:
            access_filter = ""

        update_query = text(f"""
            UPDATE email_messages 
            SET follow_up_status = :follow_up_status
            WHERE id = :email_id{access_filter}
        """)
        
        params = {
            "follow_up_status": request.follow_up_status,
            "email_id": email_id,
        }
        if current_user.role != "admin":
            params["user_id"] = current_user.id
        
        db.execute(update_query, params)
        db.commit()
        
        logger.info(f"用户 {current_user.username} 更新了邮件 {email_id} 的跟进状态为 {request.follow_up_status}")
        
        return {"success": True, "message": "更新成功"}
        
    except Exception as e:
        db.rollback()
        logger.error(f"更新跟进状态失败: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"更新跟进状态失败: {str(e)}")


@router.put("/{email_id}/need-reply")
async def update_need_reply(
    email_id: str,
    request: UpdateNeedReplyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """更新邮件需要回复状态"""
    try:
        if request.need_reply == 1 and not request.reply_text:
            raise HTTPException(status_code=400, detail="需要回复时必须填写回复内容")
        
        if current_user.role != "admin":
            access_filter = """ AND store_id IN (
                    SELECT s2.id FROM stores s2
                    WHERE s2.department_id IN (
                        SELECT ud.department_id FROM user_departments ud
                        WHERE ud.user_id = :user_id
                    )
                )"""
        else:
            access_filter = ""

        if request.need_reply == 1:
            update_query = text(f"""
                UPDATE email_messages 
                SET need_reply = 1,
                    reply_text = :reply_text,
                    reply_text_time = NOW()
                WHERE id = :email_id{access_filter}
            """)
        else:
            update_query = text(f"""
                UPDATE email_messages 
                SET need_reply = 0,
                    reply_text = '',
                    reply_text_time = NULL
                WHERE id = :email_id{access_filter}
            """)
        
        params = {
            "reply_text": request.reply_text or "",
            "email_id": email_id,
        }
        if current_user.role != "admin":
            params["user_id"] = current_user.id
        
        db.execute(update_query, params)
        db.commit()
        
        logger.info(f"用户 {current_user.username} 更新了邮件 {email_id} 的需要回复状态")
        
        if request.need_reply == 1:
            if current_user.role != "admin":
                webhook_access_filter = """ AND s.id IN (
                    SELECT s2.id FROM stores s2
                    WHERE s2.department_id IN (
                        SELECT ud.department_id FROM user_departments ud
                        WHERE ud.user_id = :user_id
                    )
                )"""
            else:
                webhook_access_filter = ""

            query = text(f"""
                SELECT
                    e.id, e.mail_subject, e.mail_content,
                    e.mail_content_chinese, e.buyer_mail_number,
                    e.ai_reply_content, e.site, e.language,
                    s.name AS store_name
                FROM email_messages e
                LEFT JOIN stores s ON e.store_id = s.id
                WHERE e.id = :email_id{webhook_access_filter}
            """)
            params = {"email_id": email_id}
            if current_user.role != "admin":
                params["user_id"] = current_user.id
            result = db.execute(query, params)
            row = result.fetchone()
            
            if row:
                webhook_data = {
                    "紫鸟账号": (row[-1] or ""),
                    "站点": get_site_name(row[-3] or ""),
                    "语言": get_language_name(row[-2] or ""),
                    "邮件主题": row[1] or "",
                    "邮件内容": row[2] or "",
                    "邮件内容-中文": row[3] or "",
                    "买家邮件": row[4] or "",
                    "AI回复内容": row[5] or "",
                    "自定义回复": request.reply_text or "",
                    "数据库ID": str(row[0]),
                }
                
                logger.info(f"发送飞书webhook: 邮件ID={email_id}")
                
                try:
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        resp = await client.post(FEISHU_WEBHOOK_URL, json=webhook_data)
                        if resp.status_code == 200:
                            logger.info(f"飞书webhook发送成功: 邮件ID={email_id}")
                        else:
                            logger.warning(f"飞书webhook发送异常: 状态码={resp.status_code}, 响应={resp.text}")
                except Exception as hook_error:
                    logger.error(f"飞书webhook发送失败: {hook_error}", exc_info=True)
        
        return {"success": True, "message": "更新成功"}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"更新需要回复状态失败: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"更新需要回复状态失败: {str(e)}")