"""开发机器人路由 - ad_product_info 表增删改查

字段映射（前端 payload -> 数据库列）：
- title            -> 标题（只填英文）
- product_title + keywords（合并） -> 关键词（只填英文）
- product_name_zh  -> 产品名提取
- reference_images -> 采购图片（逗号分隔存储于 mediumblob）
- category         -> 商品类目
- developer        -> 开发人员
- urgency          -> 产品紧急程度
- seasonality      -> 产品季节性
- competitors      -> 竞品ASIN
- purchase_price   -> 进货成本价格（采购填）
- de_title         -> 德语标题
- de_keywords      -> 德语关键词
- development_date -> 采购日期
"""
import asyncio
import json
import logging
import random
import string
from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import and_, bindparam, or_, text
from sqlalchemy.orm import Session

from database.database import get_db
from dependencies import get_current_user, get_current_admin_user
from models.ad_product_info import AdProductInfo
from models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/product-development", tags=["product-development"])

# 职能部门：可见全部数据；其余（站点组=运营部门）按 相关站点负责人员/开发人员 过滤
FUNCTIONAL_DEPARTMENTS = {"TSA部门", "管理层", "采购部", "仓库部", "美工部"}


class ProductDevelopmentPayload(BaseModel):
    title: Optional[str] = None
    product_title: Optional[str] = None
    keywords: Optional[str] = None
    product_name_zh: Optional[str] = None
    category: Optional[str] = None
    developer: Optional[str] = None
    urgency: Optional[str] = None
    seasonality: Optional[str] = None
    competitors: Optional[str] = None
    purchase_price: Optional[float] = None
    de_keywords: Optional[str] = None
    en_keywords: Optional[str] = None
    au_keywords: Optional[str] = None
    development_date: Optional[str] = None
    reference_images: Optional[List[str]] = None
    warehouse_note: Optional[str] = None
    site_owners: Optional[str] = None
    related_store: Optional[str] = None
    purchase_links: Optional[List[str]] = None
    purchase_info: Optional[str] = None
    # 采购分组对应：[{link, images[], info}]
    purchase_items: Optional[List[dict]] = None
    # 关联店铺采购数量对应：[{store, quantity}]
    store_quantities: Optional[List[dict]] = None


class BatchPayload(BaseModel):
    projects: List[ProductDevelopmentPayload]


def _merge_keywords(payload: ProductDevelopmentPayload) -> Optional[str]:
    """英文标题关键词 + 关键词 合并写入 关键词（只填英文）列"""
    parts = [payload.product_title, payload.keywords]
    merged = "，".join([p.strip() for p in parts if p and p.strip()])
    return merged or None


def _images_to_blob(images: Optional[List[str]]) -> Optional[str]:
    """图片 URL 列表 -> 逗号分隔字符串（列已改为 TEXT 存储）"""
    if not images:
        return None
    joined = ",".join([i.strip() for i in images if i and i.strip()])
    return joined or None


def _blob_to_images(blob) -> List[str]:
    """兼容 TEXT(str) 与历史 mediumblob(bytes) 两种存储"""
    if not blob:
        return []
    if isinstance(blob, bytes):
        blob = blob.decode("utf-8", errors="ignore")
    return [p.strip() for p in str(blob).split(",") if p.strip()]


def _normalize_purchase_items(items: Optional[List[dict]]) -> Optional[List[dict]]:
    """清洗采购分组数据：[{link, images[], info}]，去掉全空组"""
    if items is None:
        return None
    result = []
    for it in items:
        if not isinstance(it, dict):
            continue
        link = str(it.get("link") or "").strip()
        images = [str(i).strip() for i in (it.get("images") or []) if str(i).strip()]
        info = str(it.get("info") or "").strip()
        if link or images or info:
            result.append({"link": link or None, "images": images, "info": info or None})
    return result or None


def _parse_purchase_items(raw) -> List[dict]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _normalize_store_quantities(items: Optional[List[dict]]) -> Optional[List[dict]]:
    """清洗店铺采购数量数据：[{store, quantity}]，去掉店铺名为空的项"""
    if items is None:
        return None
    result = []
    for it in items:
        if not isinstance(it, dict):
            continue
        store = str(it.get("store") or "").strip()
        if not store:
            continue
        try:
            qty = int(it.get("quantity") or 0)
        except (TypeError, ValueError):
            qty = 0
        result.append({"store": store, "quantity": max(qty, 0)})
    return result or None


def _generate_record_id(db: Session) -> str:
    """生成 Feishu 风格的记录 ID（rec + 8 位随机码），确保不与现有副键冲突"""
    while True:
        rid = "rec" + "".join(random.choices(string.ascii_letters + string.digits, k=8))
        if not _get_by_record_id(db, rid):
            return rid


def _get_by_record_id(db: Session, record_id: str, tenant_id: Optional[int] = None) -> Optional[AdProductInfo]:
    """按业务副键 record_id 查询单条记录（表主键已改为自增 id）；传入 tenant_id 时做租户隔离"""
    q = db.query(AdProductInfo).filter(AdProductInfo.record_id == record_id)
    if tenant_id:
        q = q.filter(AdProductInfo.tenant_id == tenant_id)
    return q.first()


# ==================== 部门 / 权限 / 通知辅助 ====================

def _is_admin_user(db: Session, user: User) -> bool:
    if not user.role_id:
        return False
    row = db.execute(text("SELECT code FROM roles WHERE id = :rid AND deleted_at IS NULL"), {"rid": user.role_id}).fetchone()
    return bool(row and row[0] == "admin")


def get_user_department_names(db: Session, user: User) -> List[str]:
    rows = db.execute(text("""
        SELECT d.name FROM user_departments ud
        JOIN departments d ON d.id = ud.department_id AND d.deleted_at IS NULL
        WHERE ud.user_id = :uid AND ud.deleted_at IS NULL
    """), {"uid": user.id}).fetchall()
    return [r[0] for r in rows]


def _can_view_all(db: Session, user: User) -> bool:
    """TSA/管理层/采购/仓库/美工 及 admin 可见全部；运营（站点组）按人员过滤"""
    if _is_admin_user(db, user):
        return True
    depts = set(get_user_department_names(db, user))
    return bool(depts & FUNCTIONAL_DEPARTMENTS)


def _apply_visibility_filter(query, db: Session, user: User):
    """租户主副隔离：开发新品数据按 tenant_id 隔离，各账号只能看到本租户数据；
    运营部门再做数据拆分（相关站点负责人员 / 开发人员 包含本人昵称或用户名才可见）"""
    tid = getattr(user, "tenant_id", None)
    if tid:
        query = query.filter(AdProductInfo.tenant_id == tid)
    if _can_view_all(db, user):
        return query
    names = [n for n in {user.nickname or "", user.username or ""} if n]
    if not names:
        return query
    conds = []
    for n in names:
        conds.extend([
            AdProductInfo.site_owners.like(f"%{n}%"),
            AdProductInfo.developer.like(f"%{n}%"),
        ])
    return query.filter(or_(*conds))


def _next_product_code(db: Session, tenant_id: int) -> str:
    """生成下一个成品产品编码 BXHS-CP-#####"""
    prefix = "BXHS-CP-"
    row = db.execute(text("""
        SELECT product_code FROM products
        WHERE tenant_id = :tid AND product_code LIKE :pat AND deleted_at IS NULL
        ORDER BY CAST(SUBSTRING(product_code, :pl) AS UNSIGNED) DESC
        LIMIT 1
    """), {"tid": tenant_id, "pat": f"{prefix}%", "pl": len(prefix) + 1}).fetchone()
    next_num = 1
    if row and row[0]:
        digits = "".join(ch for ch in str(row[0])[len(prefix):] if ch.isdigit())
        if digits:
            next_num = int(digits) + 1
    return f"{prefix}{next_num:05d}"


def _match_or_create_product_code(db: Session, tenant_id: int, item: AdProductInfo) -> Optional[str]:
    """根据中文产品名称与产品库（products.name）匹配产品编码；
    未匹配到时自动在产品管理新增一个成品商品（类型=finished，产品名称=产品中文名）"""
    name = (item.product_name_zh or "").strip()
    if name:
        row = db.execute(text(
            "SELECT product_code FROM products WHERE tenant_id = :tid AND name = :name AND deleted_at IS NULL ORDER BY id LIMIT 1"
        ), {"tid": tenant_id, "name": name}).fetchone()
        if row and row[0]:
            return row[0]
        row = db.execute(text(
            "SELECT product_code FROM products WHERE tenant_id = :tid AND name LIKE :kw AND deleted_at IS NULL ORDER BY id LIMIT 1"
        ), {"tid": tenant_id, "kw": f"%{name}%"}).fetchone()
        if row and row[0]:
            return row[0]
        # 未匹配到：自动创建成品商品（产品图片待美工图片审核通过后回传）
        try:
            code = _next_product_code(db, tenant_id)
            db.execute(text("""
                INSERT INTO products (tenant_id, product_code, name, name_en, product_type, product_attribute,
                                      category, purchase_price, status, is_robot_monitored, no_accessory, local_quantity)
                VALUES (:tid, :code, :name, :name_en, 'finished', 'general',
                        :category, :price, 'active', 1, 0, 0)
            """), {
                "tid": tenant_id, "code": code, "name": name,
                "name_en": (item.title_en or "").strip() or None,
                "category": (item.category or "").strip() or None,
                "price": item.purchase_cost,
            })
            logger.info(f"新品「{name}」未匹配到产品库，已自动创建成品商品 {code}")
            return code
        except Exception as e:
            logger.warning(f"自动创建成品商品失败（不阻塞新品创建）: {e}")
            return None
    return None


def _sync_designer_images_to_product(db: Session, item: AdProductInfo) -> None:
    """美工图片审核通过后，将产品图片同步到产品管理对应商品的产品图片"""
    import json as _json
    name = (item.product_name_zh or "").strip()
    if not name:
        return
    row = db.execute(text(
        "SELECT id, images, main_image FROM products WHERE tenant_id = :tid AND name = :name AND deleted_at IS NULL ORDER BY id LIMIT 1"
    ), {"tid": item.tenant_id, "name": name}).fetchone()
    if not row:
        row = db.execute(text(
            "SELECT id, images, main_image FROM products WHERE tenant_id = :tid AND name LIKE :kw AND deleted_at IS NULL ORDER BY id LIMIT 1"
        ), {"tid": item.tenant_id, "kw": f"%{name}%"}).fetchone()
    if not row:
        return
    product_id, existing_raw, main_image = row[0], row[1], row[2]
    try:
        existing = _json.loads(existing_raw) if isinstance(existing_raw, str) and existing_raw else []
    except Exception:
        existing = []
    if not isinstance(existing, list):
        existing = []
    designer_images = _blob_to_images(item.designer_image)
    merged = list(existing)
    for img in designer_images:
        if img and img not in merged:
            merged.append(img)
    if not merged:
        return
    db.execute(text("UPDATE products SET images = :imgs, main_image = COALESCE(main_image, :main) WHERE id = :pid"),
               {"imgs": _json.dumps(merged), "main": (merged[0] if not main_image else None), "pid": product_id})


def _notify_department_users(db: Session, tenant_id: int, dept_name: str, title: str, content: str, link: str, ntype: str = "development"):
    """给某部门全部在职成员发送站内通知（KPI 消息提醒）"""
    try:
        rows = db.execute(text("""
            SELECT ud.user_id FROM user_departments ud
            JOIN departments d ON d.id = ud.department_id AND d.deleted_at IS NULL
            JOIN users u ON u.id = ud.user_id AND u.deleted_at IS NULL AND u.status = 'active'
            WHERE d.tenant_id = :tid AND d.name = :dept AND ud.deleted_at IS NULL
        """), {"tid": tenant_id, "dept": dept_name}).fetchall()
        for (uid,) in rows:
            db.execute(text("""
                INSERT INTO notifications (tenant_id, user_id, type, title, content, link, created_at, updated_at)
                VALUES (:tid, :uid, :ntype, :title, :content, :link, NOW(), NOW())
            """), {"tid": tenant_id, "uid": uid, "ntype": ntype, "title": title, "content": content, "link": link})
    except Exception as e:
        logger.warning(f"发送部门通知失败（不阻塞主流程）: {e}")


def _notify_new_product(db: Session, tenant_id: int, item: AdProductInfo):
    """新品创建后按部门推送 KPI 消息：采购填单价 / 仓库传六面图+备注 / 美工传产品图"""
    name = item.product_name_zh or item.title_en or item.record_id
    link = f"/development?record={item.record_id}"
    _notify_department_users(
        db, tenant_id, "采购部",
        "新品采购单价填写提醒",
        f"新品「{name}」已创建，请填写采购单价并确认是否采购。",
        f"{link}&tab=purchase",
    )
    _notify_department_users(
        db, tenant_id, "仓库部",
        "新品六面图上传提醒",
        f"新品「{name}」已创建，请在添加新品页上传六面图并填写仓库备注。",
        f"/development/new?record={item.record_id}&tab=warehouse",
    )
    _notify_department_users(
        db, tenant_id, "美工部",
        "新品产品图片上传提醒",
        f"新品「{name}」已创建，请上传产品图片。",
        f"{link}&tab=designer",
    )


def _product_sku_asin_map(db: Session, tenant_id: int, items: List[AdProductInfo]) -> dict:
    """批量查询产品库平台商品：按产品编码优先、中文名兜底，
    返回 {产品编码或中文名: [{store, sku, asin}, ...]}（含店铺对应）"""
    codes = {i.product_code for i in items if i.product_code}
    names = {i.product_name_zh for i in items if i.product_name_zh}
    mapping: dict = {}
    if not codes and not names:
        return mapping
    rows = db.execute(text(
        "SELECT p.product_code AS pcode, p.name AS pname, s.name AS store_name, pp.sku, pp.asin "
        "FROM products p "
        "JOIN platform_products pp ON pp.product_id = p.id AND pp.deleted_at IS NULL AND pp.tenant_id = p.tenant_id "
        "LEFT JOIN stores s ON JSON_CONTAINS(pp.store_id, CAST(s.id AS CHAR)) AND s.deleted_at IS NULL "
        "WHERE p.tenant_id = :tid AND p.deleted_at IS NULL "
        "AND ((p.product_code != '' AND p.product_code IN :codes) OR (p.name != '' AND p.name IN :names)) "
        "ORDER BY p.id, pp.id"
    ).bindparams(tid=tenant_id)
     .bindparams(bindparam("codes", expanding=True, value=list(codes) or ["__none__"]))
     .bindparams(bindparam("names", expanding=True, value=list(names) or ["__none__"])),
     {"codes": list(codes) or ["__none__"], "names": list(names) or ["__none__"]}).fetchall()
    for pcode, pname, store_name, sku, asin in rows:
        entry = {"store": store_name, "sku": sku, "asin": asin}
        for key in (pcode, pname):
            if not key:
                continue
            lst = mapping.setdefault(key, [])
            if entry not in lst:
                lst.append(entry)
    return mapping


def _serialize(item: AdProductInfo, platform_items: Optional[List[dict]] = None) -> dict:
    return {
        "record_id": item.record_id,
        "title": item.title_en,
        "keywords": item.keywords_en,
        "product_name_zh": item.product_name_zh,
        "purchase_images": _blob_to_images(item.purchase_images),
        "category": item.category,
        "developer": item.developer,
        "urgency": item.urgency,
        "seasonality": item.seasonality,
        "competitors": item.competitors,
        "purchase_price": item.purchase_cost,
        "purchased": item.purchased or "未采购",
        "purchase_note": item.purchase_note,
        "product_code": item.product_code,
        "platform_items": platform_items or [],
        "site_owners": item.site_owners,
        "related_store": item.related_store,
        "purchase_links": [l for l in str(item.purchase_links or "").split("\n") if l.strip()] or None,
        "purchase_info": item.purchase_info,
        "purchase_items": _parse_purchase_items(item.purchase_items_json),
        "store_quantities": _parse_purchase_items(item.store_quantity_json),
        "warehouse_note": item.warehouse_note,
        "six_face_images": _blob_to_images(item.six_face_image),
        "designer_images": _blob_to_images(item.designer_image),
        "designer_complete_date": item.designer_complete_date,
        "image_review": item.image_review,
        "de_keywords": item.de_keywords,
        "en_keywords": item.en_keywords,
        "au_keywords": item.au_keywords,
        "development_date": item.purchase_date,
        "doc_name": item.doc_name,
        "doc_link": item.doc_link,
    }


@router.get("/projects")
async def list_projects(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(12, ge=1, le=100, description="每页数量"),
    search: Optional[str] = Query(None, description="搜索英文标题/关键词/中文名称/类目"),
    category: Optional[str] = Query(None, description="类目筛选"),
    urgency: Optional[str] = Query(None, description="紧急程度筛选"),
    seasonality: Optional[str] = Query(None, description="季节性筛选"),
    developer: Optional[str] = Query(None, description="开发人员筛选"),
    date: Optional[str] = Query(None, description="日期筛选（YYYY-MM-DD）"),
    date_start: Optional[str] = Query(None, description="日期范围开始（YYYY-MM-DD）"),
    date_end: Optional[str] = Query(None, description="日期范围结束（YYYY-MM-DD）"),
    status: Optional[str] = Query(None, description="状态筛选: purchase/six_face/designer/review/review_rejected/reviewed/not_ordered/purchased"),
    missing: Optional[str] = Query(None, description="信息缺失筛选：字段 key，筛选出该字段为空的记录"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """分页查询开发新品记录（按部门做数据可见性拆分）"""
    try:
        query = db.query(AdProductInfo)
        if search and search.strip():
            # 搜索范围：产品中文名 + 平台商品的 ASIN/SKU（ad_product_info.product_code → products → platform_products）
            kw = f"%{search.strip()}%"
            sub = text(
                "SELECT p.product_code FROM products p "
                "JOIN platform_products pp ON pp.product_id = p.id AND pp.deleted_at IS NULL AND pp.tenant_id = p.tenant_id "
                "WHERE p.deleted_at IS NULL AND (pp.asin LIKE :kw OR pp.sku LIKE :kw) AND p.product_code != ''"
            )
            query = query.filter(
                or_(
                    AdProductInfo.product_name_zh.like(kw),
                    AdProductInfo.product_code.in_(sub),
                )
            )
            query = query.params(kw=kw)
        if category:
            query = query.filter(AdProductInfo.category == category)
        if urgency:
            query = query.filter(AdProductInfo.urgency == urgency)
        if seasonality:
            # 季节性字段是逗号分隔多值，包含该选项即命中
            query = query.filter(AdProductInfo.seasonality.contains(seasonality, autoescape=True))
        if developer:
            query = query.filter(AdProductInfo.developer == developer)
        if date:
            # 日期范围筛选：支持 date=YYYY-MM-DD 或 date_start/date_end
            if date_start or date_end:
                if date_start:
                    query = query.filter(AdProductInfo.purchase_date >= date_start)
                if date_end:
                    query = query.filter(AdProductInfo.purchase_date <= date_end)
            else:
                query = query.filter(AdProductInfo.purchase_date == date)
        # 状态筛选：支持逗号分隔多选，多选之间取并集（OR）
        def _status_cond(s: str):
            if s == "purchase":
                return AdProductInfo.purchase_cost.is_(None)
            if s == "six_face":
                return AdProductInfo.six_face_image.is_(None)
            if s == "six_face_uploaded":
                # 已上传六面图：六面图字段非空
                return and_(AdProductInfo.six_face_image.isnot(None), AdProductInfo.six_face_image != "")
            if s == "designer":
                return AdProductInfo.designer_image.is_(None)
            if s == "review":
                return and_(
                    AdProductInfo.designer_image.isnot(None),
                    or_(AdProductInfo.image_review.is_(None), AdProductInfo.image_review == ""),
                )
            if s == "review_rejected":
                # 美工未通过：已上传产品图片但审核结果包含「不通过」
                return and_(
                    AdProductInfo.designer_image.isnot(None),
                    AdProductInfo.image_review.like("%不通过%"),
                )
            if s == "not_ordered":
                # 待生成采购单：尚未生成采购单（也未被采购流程标记为已采购）
                return or_(AdProductInfo.purchased.is_(None), AdProductInfo.purchased == "", AdProductInfo.purchased == "未采购")
            if s == "reviewed":
                # 图片已审核：已上传产品图且审核通过（含「通过」且不含「不通过」）
                return and_(
                    AdProductInfo.designer_image.isnot(None),
                    AdProductInfo.image_review.like("%通过%"),
                    AdProductInfo.image_review.notlike("%不通过%"),
                )
            if s == "purchased":
                # 产品已采购：采购流程已标记为已采购
                return AdProductInfo.purchased == "已采购"
            return None

        if status:
            conds = [c for c in (_status_cond(s.strip()) for s in status.split(",")) if c is not None]
            if conds:
                query = query.filter(or_(*conds))
        # 信息缺失筛选：指定字段为空的记录（支持逗号分隔多选，任一字段缺失即命中）
        _MISSING_FIELD_FILTERS = {
            "title": AdProductInfo.title_en,
            "product_name_zh": AdProductInfo.product_name_zh,
            "category": AdProductInfo.category,
            "keywords": AdProductInfo.keywords_en,
            "purchase_price": AdProductInfo.purchase_cost,
            "related_store": AdProductInfo.related_store,
            "purchase_links": AdProductInfo.purchase_links,
            "purchase_items": AdProductInfo.purchase_items_json,
            "warehouse_note": AdProductInfo.warehouse_note,
            "site_owners": AdProductInfo.site_owners,
            "competitors": AdProductInfo.competitors,
            "de_keywords": AdProductInfo.de_keywords,
            "en_keywords": AdProductInfo.en_keywords,
            "au_keywords": AdProductInfo.au_keywords,
        }
        if missing:
            m_conds = []
            for m in (x.strip() for x in missing.split(",")):
                col = _MISSING_FIELD_FILTERS.get(m)
                if col is not None:
                    m_conds.append(or_(col.is_(None), col == ""))
            if m_conds:
                query = query.filter(or_(*m_conds))
        query = _apply_visibility_filter(query, db, current_user)
        total = query.count()
        items = (
            query.order_by(AdProductInfo.purchase_date.desc(), AdProductInfo.record_id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        # 批量匹配产品库平台商品（店铺 + SKU/ASIN，编码优先，中文名兜底）
        tenant_id = getattr(current_user, "tenant_id", 1) or 1
        sku_asin_map = _product_sku_asin_map(db, tenant_id, items)

        def _ser(i: AdProductInfo) -> dict:
            return _serialize(i, sku_asin_map.get(i.product_code or i.product_name_zh or "", []))

        return {
            "success": True,
            "data": {
                "items": [_ser(i) for i in items],
                "total": total,
                "page": page,
                "page_size": page_size,
            },
        }
    except Exception as e:
        logger.error(f"查询开发新品列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"查询开发新品列表失败: {str(e)}")


def _create_one(db: Session, tenant_id: int, payload: ProductDevelopmentPayload) -> AdProductInfo:
    items = _normalize_purchase_items(payload.purchase_items)
    item = AdProductInfo(
        record_id=_generate_record_id(db),
        title_en=payload.title,
        keywords_en=_merge_keywords(payload),
        en_keywords=payload.en_keywords,
        au_keywords=payload.au_keywords,
        product_name_zh=payload.product_name_zh,
        purchase_images=_images_to_blob(payload.reference_images),
        category=payload.category,
        developer=payload.developer,
        urgency=payload.urgency,
        seasonality=payload.seasonality,
        competitors=payload.competitors,
        purchase_cost=payload.purchase_price,
        purchased="已采购" if payload.purchase_price else "未采购",
        product_code=None,
        site_owners=payload.site_owners,
        related_store=payload.related_store,
        purchase_links="\n".join([l.strip() for l in (payload.purchase_links or []) if l and l.strip()]) or None,
        purchase_info=(payload.purchase_info or "").strip() or None,
        purchase_items_json=json.dumps(items, ensure_ascii=False) if items else None,
        store_quantity_json=json.dumps(sqs, ensure_ascii=False) if (sqs := _normalize_store_quantities(payload.store_quantities)) else None,
        warehouse_note=payload.warehouse_note,
        de_keywords=payload.de_keywords,
        purchase_date=payload.development_date,
    )
    db.add(item)
    db.flush()
    # 根据中文产品名称与现有商品匹配产品编码，未匹配到则生成新编码
    item.product_code = _match_or_create_product_code(db, tenant_id, item)
    return item


@router.get("/projects/filters")
async def get_filter_options(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取筛选栏各下拉项的可选值（取自数据库去重）"""
    try:
        def distinct(col):
            vals = [r[0] for r in db.query(col).distinct().all() if r[0] and str(r[0]).strip()]
            return sorted(set(vals))

        def distinct_split(col):
            """字段值按逗号拆分后去重（用于季节性等逗号分隔多值字段）"""
            parts = set()
            for (v,) in db.query(col).distinct().all():
                if not v:
                    continue
                for p in re.split(r"[,，、]", str(v)):
                    p = p.strip()
                    if p:
                        parts.add(p)
            return sorted(parts)

        return {
            "success": True,
            "data": {
                "categories": distinct(AdProductInfo.category),
                "urgencies": distinct(AdProductInfo.urgency),
                "seasonalities": distinct_split(AdProductInfo.seasonality),
                "developers": distinct(AdProductInfo.developer),
            },
        }
    except Exception as e:
        logger.error(f"获取筛选选项失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取筛选选项失败: {str(e)}")


# ==================== 自定义类目管理 ====================

class CategoryCreate(BaseModel):
    name: str


@router.get("/categories")
async def get_categories(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取自定义类目列表（与内置类目合并展示由前端处理）"""
    rows = db.execute(text(
        "SELECT name FROM ad_product_categories WHERE tenant_id = :tid ORDER BY id"
    ), {"tid": current_user.tenant_id}).fetchall()
    return {"success": True, "data": [r[0] for r in rows]}


@router.post("/categories")
async def add_category(
    payload: CategoryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """新增自定义类目（仅管理人员）"""
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="类目名称不能为空")
    if len(name) > 100:
        raise HTTPException(status_code=400, detail="类目名称过长")
    exists = db.execute(text(
        "SELECT id FROM ad_product_categories WHERE tenant_id = :tid AND name = :name"
    ), {"tid": current_user.tenant_id, "name": name}).fetchone()
    if exists:
        return {"success": True, "data": {"name": name}, "message": "类目已存在"}
    db.execute(text(
        "INSERT INTO ad_product_categories (tenant_id, name) VALUES (:tid, :name)"
    ), {"tid": current_user.tenant_id, "name": name})
    db.commit()
    return {"success": True, "data": {"name": name}, "message": "类目已添加"}


# ==================== 新品模板导入/导出 ====================

# 与前端 CATEGORY_OPTIONS 保持一致的内置类目
BUILTIN_CATEGORIES = [
    "BALLOON", "CAKE TOPPER", "PARTY SUPPLIES", "PARTY FAVOR", "DISHWARE PLATE",
    "ADULT COSTUME", "BANNER", "BACKDROP", "GIFT WRAP", "DISPOSABLE NAPKIN",
    "HANDBAG", "FIGURINE", "COSTUME HEADWEAR", "PET SUPPLIES", "ART CRAFT KIT",
    "CARRIER BAG CASE", "COSTUME OUTFIT",
]
BUILTIN_SEASONALITIES = ["日常", "夏季", "圣诞节", "万圣节", "狂欢节", "跨年", "冬季", "感恩节", "复活节", "5月毕业季", "斋月", "四叶草", "独立日"]
BUILTIN_URGENCIES = ["一般", "十分紧急"]

IMPORT_COLUMNS = [
    "英文标题", "英文标题关键词", "产品中文名称", "类目", "产品季节性", "产品紧急度", "日期",
    "进货价", "竞品ASIN", "德语关键词", "英语关键词", "澳洲关键词", "关联店铺", "相关站点负责人员",
    "仓库备注", "采购链接", "采购信息",
]


@router.get("/import-template")
async def download_import_template(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """下载新品导入模板（xlsx，类目/季节性/紧急度/关联店铺列带下拉选项）"""
    import io
    from openpyxl import Workbook
    from openpyxl.worksheet.datavalidation import DataValidation
    from fastapi.responses import StreamingResponse

    # 汇总选项
    custom = [r[0] for r in db.execute(text(
        "SELECT name FROM ad_product_categories WHERE tenant_id = :tid ORDER BY id"
    ), {"tid": current_user.tenant_id}).fetchall()]
    categories = BUILTIN_CATEGORIES + [c for c in custom if c not in BUILTIN_CATEGORIES]
    stores = [r[0] for r in db.execute(text(
        "SELECT name FROM store_groups WHERE tenant_id = :tid AND name IS NOT NULL AND name != '' ORDER BY name"
    ), {"tid": current_user.tenant_id}).fetchall()] if _has_table(db, "store_groups") else []

    wb = Workbook()
    ws = wb.active
    ws.title = "新品导入"
    for col_idx, name in enumerate(IMPORT_COLUMNS, start=1):
        cell = ws.cell(row=1, column=col_idx, value=name)
        cell.font = cell.font.copy(bold=True)
        cell.fill = cell.fill.copy(start_color="E0E7FF", end_color="E0E7FF", fill_type="solid")
    ws.cell(row=2, column=1, value="Silver Balloon Party Set")
    ws.cell(row=2, column=2, value="balloon, party, silver")
    ws.cell(row=2, column=3, value="银色气球派对套装")
    ws.cell(row=2, column=4, value="BALLOON")
    ws.cell(row=2, column=5, value="日常")
    ws.cell(row=2, column=6, value="一般")
    ws.cell(row=2, column=7, value="2026-10-14")
    ws.cell(row=2, column=8, value=9.9)
    ws.cell(row=2, column=9, value="B0XXXXXXXX")
    ws.cell(row=2, column=10, value="luftballons, silber")
    ws.cell(row=2, column=11, value="balloon, party")
    ws.cell(row=2, column=12, value="balloon, party")
    ws.cell(row=2, column=13, value="美东×100、美西×50")
    ws.cell(row=2, column=16, value="https://detail.1688.com/example1.html\nhttps://detail.1688.com/example2.html")
    ws.cell(row=2, column=17, value="供应商A/交期7天\n供应商B/交期15天")
    ws.freeze_panes = "A2"

    # 选项 sheet + 数据验证下拉
    opt = wb.create_sheet("选项")
    option_groups = [("类目", categories), ("季节性", BUILTIN_SEASONALITIES), ("紧急度", BUILTIN_URGENCIES), ("店铺", stores)]
    for gi, (title, values) in enumerate(option_groups, start=1):
        opt.cell(row=1, column=gi, value=title).font = opt.cell(row=1, column=gi).font.copy(bold=True)
        for ri, v in enumerate(values, start=2):
            opt.cell(row=ri, column=gi, value=v)
        col_letter = opt.cell(row=1, column=gi).column_letter
        if values:
            dv = DataValidation(type="list", formula1=f"='选项'!${col_letter}$2:${col_letter}${len(values) + 1}", allow_blank=True, showErrorMessage=False)
            ws.add_data_validation(dv)
            header = IMPORT_COLUMNS[3] if title == "类目" else None
            target_cols = {"类目": "类目", "季节性": "产品季节性", "紧急度": "产品紧急度", "店铺": "关联店铺"}[title]
            col_idx = IMPORT_COLUMNS.index(target_cols) + 1
            letter = ws.cell(row=1, column=col_idx).column_letter
            dv.add(f"{letter}2:{letter}1000")

    # 填写说明 sheet
    guide = wb.create_sheet("填写说明")
    guide.column_dimensions["A"].width = 16
    guide.column_dimensions["B"].width = 100
    guide_rows = [
        ("列名", "填写说明"),
        ("英文标题", "必填；最长 75 个字符，超长会被校验拦截"),
        ("英文标题关键词", "选填；英文搜索关键词，多个用英文逗号分隔"),
        ("产品中文名称", "必填；用于匹配产品库产品编码，未匹配时保存后会自动在产品库新增成品商品"),
        ("类目", "必填；从下拉选项中选择（下拉含全部可用类目）"),
        ("产品季节性", "选填；从下拉选项中选择，默认「日常」"),
        ("产品紧急度", "选填；下拉选择「一般」或「十分紧急」，默认「一般」"),
        ("日期", "选填；开发日期，格式 YYYY-MM-DD（如 2026-10-14）"),
        ("进货价", "选填；数字，不能为负数"),
        ("竞品ASIN", "选填；多个用逗号分隔"),
        ("德语关键词", "选填；德语搜索关键词，多个用英文逗号分隔"),
        ("英语关键词", "选填；英语搜索关键词，多个用英文逗号分隔"),
        ("澳洲关键词", "选填；澳洲站搜索关键词，多个用英文逗号分隔"),
        ("关联店铺", "选填；只需填到店铺分组一级。支持多组，用顿号/逗号分隔；每组可带数量：分组名×数量（如 美东×100、美西×50），不带数量则默认 0。可从下拉选择分组名后再补 ×数量"),
        ("相关站点负责人员", "选填；多个用逗号分隔"),
        ("仓库备注", "选填；给仓库的备注说明"),
        ("采购链接", "选填；可填多行（单元格内 Alt+Enter 换行），每行一个采购链接；也可用分号分隔"),
        ("采购信息", "选填；可填多行（单元格内 Alt+Enter 换行），与「采购链接」按行一一对应成组：第 1 行信息对应第 1 行链接，依此类推"),
        ("采购图片", "无法随 Excel 导入；保存发布后在开发机器人卡片/采购填写弹窗中补充上传（支持拖拽与粘贴）"),
        ("整体规则", "一行一条新品；导入解析后数据填入「添加新品」表单作为草稿，检查编辑后点保存才正式入库并推送部门提醒，导入本身不会直接发布"),
    ]
    for ri, (k, v) in enumerate(guide_rows, start=1):
        a = guide.cell(row=ri, column=1, value=k)
        b = guide.cell(row=ri, column=2, value=v)
        a.alignment = a.alignment.copy(vertical="top", wrap_text=True)
        b.alignment = b.alignment.copy(vertical="top", wrap_text=True)
        if ri == 1:
            a.font = a.font.copy(bold=True)
            b.font = b.font.copy(bold=True)
            a.fill = a.fill.copy(start_color="E0E7FF", end_color="E0E7FF", fill_type="solid")
            b.fill = b.fill.copy(start_color="E0E7FF", end_color="E0E7FF", fill_type="solid")

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=ad_product_import_template.xlsx"},
    )


def _has_table(db: Session, table: str) -> bool:
    try:
        row = db.execute(text("SHOW TABLES LIKE :t"), {"t": table}).fetchone()
        return bool(row)
    except Exception:
        return False


@router.post("/import-projects")
async def import_projects(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """上传新品导入模板（xlsx），按行批量创建新品并推送部门通知"""
    import io
    from openpyxl import load_workbook

    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=400, detail="请上传 .xlsx 格式的模板文件")
    content = await file.read()
    try:
        wb = load_workbook(io.BytesIO(content), data_only=True)
    except Exception:
        raise HTTPException(status_code=400, detail="无法解析文件，请使用下载的导入模板填写")
    ws = wb.active

    # 表头 → 列号
    header_map: dict = {}
    for cell in next(ws.iter_rows(min_row=1, max_row=1)):
        if cell.value:
            header_map[str(cell.value).strip()] = cell.column

    def cell_val(row: int, header: str):
        col = header_map.get(header)
        if not col:
            return None
        v = ws.cell(row=row, column=col).value
        if v is None:
            return None
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v

    # 可选值集合（用于字段异常检测）
    valid_categories = set(BUILTIN_CATEGORIES) | {
        r[0] for r in db.execute(text(
            "SELECT name FROM ad_product_categories WHERE tenant_id = :tid"
        ), {"tid": current_user.tenant_id}).fetchall()
    }
    valid_urgencies = set(BUILTIN_URGENCIES)

    created, errors = 0, []
    notify_items = []
    for row_idx in range(2, ws.max_row + 1):
        title = cell_val(row_idx, "英文标题")
        name_zh = cell_val(row_idx, "产品中文名称")
        if not title and not name_zh:
            continue  # 空行

        # ===== 导入数据检测（必填/长度/格式/选项）=====
        row_errors: List[str] = []
        title_str = str(title or "").strip()
        name_str = str(name_zh or "").strip()
        if not title_str:
            row_errors.append("英文标题未填写")
        elif len(title_str) > 75:
            row_errors.append(f"英文标题超过75个字符（当前{len(title_str)}个）")
        if not name_str:
            row_errors.append("产品中文名称未填写")

        category_val = cell_val(row_idx, "类目")
        category_str = str(category_val or "").strip()
        if not category_str:
            row_errors.append("类目未填写")
        elif category_str not in valid_categories:
            row_errors.append(f"类目「{category_str}」不在可选类目中")

        urgency_val = cell_val(row_idx, "产品紧急度")
        urgency_str = str(urgency_val or "").strip() or "一般"
        if urgency_str not in valid_urgencies:
            row_errors.append(f"产品紧急度「{urgency_str}」不是有效选项（一般/十分紧急）")

        price_val = cell_val(row_idx, "进货价")
        price = None
        if price_val not in (None, ""):
            try:
                price = float(price_val)
                if price < 0:
                    row_errors.append(f"进货价不能为负数（{price_val}）")
            except (TypeError, ValueError):
                row_errors.append(f"进货价不是有效数字：{price_val}")

        date_val = cell_val(row_idx, "日期")
        date_str = ""
        if date_val:
            if hasattr(date_val, "strftime"):
                date_str = date_val.strftime("%Y-%m-%d")
            else:
                date_str = str(date_val)[:10]
                try:
                    datetime.strptime(date_str, "%Y-%m-%d")
                except ValueError:
                    row_errors.append(f"日期格式异常：{date_val}（应为 YYYY-MM-DD）")

        if row_errors:
            errors.append({"row": row_idx, "msg": "；".join(row_errors)})
            continue

        try:
            links_raw = cell_val(row_idx, "采购链接") or ""
            links = [p.strip() for p in re.split(r"[\n;；]", str(links_raw)) if p.strip()]
            payload = ProductDevelopmentPayload(
                title=title_str,
                product_title=str(cell_val(row_idx, "英文标题关键词") or ""),
                product_name_zh=name_str,
                category=category_str,
                developer=current_user.nickname or current_user.username,
                urgency=urgency_str,
                seasonality=str(cell_val(row_idx, "产品季节性") or "") or None,
                competitors=str(cell_val(row_idx, "竞品ASIN") or "") or None,
                purchase_price=price,
                de_keywords=str(cell_val(row_idx, "德语关键词") or "") or None,
                en_keywords=str(cell_val(row_idx, "英语关键词") or "") or None,
                au_keywords=str(cell_val(row_idx, "澳洲关键词") or "") or None,
                development_date=date_str or None,
                related_store=str(cell_val(row_idx, "关联店铺") or "") or None,
                site_owners=str(cell_val(row_idx, "相关站点负责人员") or "") or None,
                warehouse_note=str(cell_val(row_idx, "仓库备注") or "") or None,
                purchase_links=links or None,
                purchase_info=str(cell_val(row_idx, "采购信息") or "") or None,
                reference_images=[],
            )
            item = _create_one(db, current_user.tenant_id, payload)
            db.flush()
            notify_items.append(item)
            created += 1
        except Exception as ex:
            errors.append({"row": row_idx, "msg": str(ex)[:120]})
    db.commit()
    for it in notify_items:
        _notify_new_product(db, current_user.tenant_id, it)
    db.commit()
    return {"success": True, "data": {"created": created, "failed": len(errors), "errors": errors}}


@router.post("/import-projects/parse")
async def import_projects_parse(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """解析导入模板并校验，返回草稿行（不入库、不推送通知）。
    前端将草稿行填入「添加新品」页面，用户编辑确认后再保存发布。"""
    import io
    from openpyxl import load_workbook

    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=400, detail="请上传 .xlsx 格式的模板文件")
    content = await file.read()
    try:
        wb = load_workbook(io.BytesIO(content), data_only=True)
    except Exception:
        raise HTTPException(status_code=400, detail="无法解析文件，请使用下载的导入模板填写")
    ws = wb.active

    header_map: dict = {}
    for cell in next(ws.iter_rows(min_row=1, max_row=1)):
        if cell.value:
            header_map[str(cell.value).strip()] = cell.column

    def cell_val(row: int, header: str):
        col = header_map.get(header)
        if not col:
            return None
        v = ws.cell(row=row, column=col).value
        if v is None:
            return None
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v

    valid_categories = set(BUILTIN_CATEGORIES) | {
        r[0] for r in db.execute(text(
            "SELECT name FROM ad_product_categories WHERE tenant_id = :tid"
        ), {"tid": current_user.tenant_id}).fetchall()
    }
    valid_urgencies = set(BUILTIN_URGENCIES)

    def parse_store_quantities(raw: str) -> List[dict]:
        """关联店铺支持多组：「分组×数量」「分组:数量」「分组」，顿号/逗号/换行分隔"""
        out = []
        for part in re.split(r"[、，,;；\n]+", str(raw or "")):
            part = part.strip()
            if not part:
                continue
            m = re.match(r"^(.+?)[×xX*:：](\d+(?:\.\d+)?)$", part)
            if m:
                out.append({"store": m.group(1).strip(), "quantity": int(float(m.group(2)))})
            else:
                out.append({"store": part, "quantity": 0})
        return out

    def parse_purchase_items(links_raw, info_raw) -> List[dict]:
        """采购链接与采购信息按行一一对应成组（图片无法随 Excel 导入，留空）"""
        links = [p.strip() for p in re.split(r"[\n;；]+", str(links_raw or "")) if p.strip()]
        infos = [p.strip() for p in re.split(r"\n+", str(info_raw or "")) if p.strip()]
        n = max(len(links), len(infos))
        return [{"link": links[i] if i < len(links) else "",
                 "info": infos[i] if i < len(infos) else ""} for i in range(n)]

    rows: List[dict] = []
    errors: List[dict] = []
    for row_idx in range(2, ws.max_row + 1):
        title = cell_val(row_idx, "英文标题")
        name_zh = cell_val(row_idx, "产品中文名称")
        if not title and not name_zh:
            continue

        row_errors: List[str] = []
        title_str = str(title or "").strip()
        name_str = str(name_zh or "").strip()
        if not title_str:
            row_errors.append("英文标题未填写")
        elif len(title_str) > 75:
            row_errors.append(f"英文标题超过75个字符（当前{len(title_str)}个）")
        if not name_str:
            row_errors.append("产品中文名称未填写")

        category_str = str(cell_val(row_idx, "类目") or "").strip()
        if not category_str:
            row_errors.append("类目未填写")
        elif category_str not in valid_categories:
            row_errors.append(f"类目「{category_str}」不在可选类目中")

        urgency_str = str(cell_val(row_idx, "产品紧急度") or "").strip() or "一般"
        if urgency_str not in valid_urgencies:
            row_errors.append(f"产品紧急度「{urgency_str}」不是有效选项（一般/十分紧急）")

        price = None
        price_val = cell_val(row_idx, "进货价")
        if price_val not in (None, ""):
            try:
                price = float(price_val)
                if price < 0:
                    row_errors.append(f"进货价不能为负数（{price_val}）")
            except (TypeError, ValueError):
                row_errors.append(f"进货价不是有效数字：{price_val}")

        date_val = cell_val(row_idx, "日期")
        date_str = ""
        if date_val:
            if hasattr(date_val, "strftime"):
                date_str = date_val.strftime("%Y-%m-%d")
            else:
                date_str = str(date_val)[:10]
                try:
                    datetime.strptime(date_str, "%Y-%m-%d")
                except ValueError:
                    row_errors.append(f"日期格式异常：{date_val}（应为 YYYY-MM-DD）")

        if row_errors:
            errors.append({"row": row_idx, "msg": "；".join(row_errors)})
            continue

        rows.append({
            "title": title_str,
            "product_title": str(cell_val(row_idx, "英文标题关键词") or ""),
            "product_name_zh": name_str,
            "category": category_str,
            "seasonality": str(cell_val(row_idx, "产品季节性") or "") or "日常",
            "urgency": urgency_str,
            "development_date": date_str,
            "purchase_price": price,
            "competitors": str(cell_val(row_idx, "竞品ASIN") or ""),
            "de_keywords": str(cell_val(row_idx, "德语关键词") or ""),
            "en_keywords": str(cell_val(row_idx, "英语关键词") or ""),
            "au_keywords": str(cell_val(row_idx, "澳洲关键词") or ""),
            "store_quantities": parse_store_quantities(cell_val(row_idx, "关联店铺") or ""),
            "site_owners": str(cell_val(row_idx, "相关站点负责人员") or ""),
            "warehouse_note": str(cell_val(row_idx, "仓库备注") or ""),
            "purchase_items": parse_purchase_items(cell_val(row_idx, "采购链接"), cell_val(row_idx, "采购信息")),
        })
    return {"success": True, "data": {"rows": rows, "failed": len(errors), "errors": errors}}


@router.post("/projects")
async def create_project(
    payload: ProductDevelopmentPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """新增一条开发新品记录"""
    try:
        item = _create_one(db, current_user.tenant_id, payload)
        db.commit()
        db.refresh(item)
        _notify_new_product(db, current_user.tenant_id, item)
        db.commit()
        return {"success": True, "data": _serialize(item)}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"新增开发新品失败: {e}")
        raise HTTPException(status_code=500, detail=f"新增开发新品失败: {str(e)}")


@router.post("/projects/batch")
async def create_projects_batch(
    payload: BatchPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """批量新增开发新品记录"""
    try:
        items = [_create_one(db, current_user.tenant_id, p) for p in payload.projects]
        db.commit()
        for i in items:
            _notify_new_product(db, current_user.tenant_id, i)
        db.commit()
        return {"success": True, "data": [_serialize(i) for i in items]}
    except Exception as e:
        db.rollback()
        logger.error(f"批量新增开发新品失败: {e}")
        raise HTTPException(status_code=500, detail=f"批量新增开发新品失败: {str(e)}")


@router.put("/projects/{record_id}")
async def update_project(
    record_id: str,
    payload: ProductDevelopmentPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新开发新品记录（仅更新传入的字段）"""
    try:
        item = _get_by_record_id(db, record_id, current_user.tenant_id)
        if not item:
            raise HTTPException(status_code=404, detail="记录不存在")

        if payload.title is not None:
            item.title_en = payload.title
        if payload.product_title is not None or payload.keywords is not None:
            pt = payload.product_title or ""
            kw = payload.keywords or ""
            merged = "，".join([p.strip() for p in [pt, kw] if p and p.strip()])
            item.keywords_en = merged or None
        if payload.product_name_zh is not None:
            item.product_name_zh = payload.product_name_zh
        if payload.reference_images is not None:
            item.purchase_images = _images_to_blob(payload.reference_images)
        if payload.category is not None:
            item.category = payload.category
        if payload.developer is not None:
            item.developer = payload.developer
        if payload.urgency is not None:
            item.urgency = payload.urgency
        if payload.seasonality is not None:
            item.seasonality = payload.seasonality
        if payload.competitors is not None:
            item.competitors = payload.competitors
        if payload.purchase_price is not None:
            item.purchase_cost = payload.purchase_price
        if payload.de_keywords is not None:
            item.de_keywords = payload.de_keywords
        if payload.en_keywords is not None:
            item.en_keywords = payload.en_keywords
        if payload.au_keywords is not None:
            item.au_keywords = payload.au_keywords
        if payload.development_date is not None:
            item.purchase_date = payload.development_date
        if payload.warehouse_note is not None:
            item.warehouse_note = payload.warehouse_note
        if payload.site_owners is not None:
            item.site_owners = payload.site_owners
        if payload.related_store is not None:
            item.related_store = payload.related_store
        if payload.purchase_links is not None:
            item.purchase_links = "\n".join([l.strip() for l in payload.purchase_links if l and l.strip()]) or None
        if payload.purchase_info is not None:
            item.purchase_info = (payload.purchase_info or "").strip() or None
        if payload.purchase_items is not None:
            items = _normalize_purchase_items(payload.purchase_items)
            item.purchase_items_json = json.dumps(items, ensure_ascii=False) if items else None
        if payload.store_quantities is not None:
            sqs = _normalize_store_quantities(payload.store_quantities)
            item.store_quantity_json = json.dumps(sqs, ensure_ascii=False) if sqs else None

        db.commit()
        return {"success": True, "data": _serialize(item)}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"更新开发新品失败: {e}")
        raise HTTPException(status_code=500, detail=f"更新开发新品失败: {str(e)}")


@router.delete("/projects/{record_id}")
async def delete_project(
    record_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """删除开发新品记录（物理删除，仅管理员）"""
    try:
        item = _get_by_record_id(db, record_id, current_user.tenant_id)
        if not item:
            raise HTTPException(status_code=404, detail="记录不存在")
        db.delete(item)
        db.commit()
        return {"success": True, "data": {"record_id": record_id}}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"删除开发新品失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除开发新品失败: {str(e)}")


# ==================== 角色 / KPI 统计 / 部门协作端点 ====================

@router.get("/my-roles")
async def get_my_roles(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """返回当前用户部门与开发机器人可执行的操作（用于前端按角色显示按钮）"""
    departments = get_user_department_names(db, current_user)
    is_admin = _is_admin_user(db, current_user)
    view_all = _can_view_all(db, current_user)
    return {
        "success": True,
        "data": {
            "departments": departments,
            "is_admin": is_admin,
            "can_view_all": view_all,
            "can_purchase": is_admin or view_all or "采购部" in departments,
            "can_warehouse": is_admin or view_all or "仓库部" in departments,
            "can_design": is_admin or view_all or "美工部" in departments,
            "can_review": is_admin or "管理层" in departments or "TSA部门" in departments,
            "can_replenish": is_admin or view_all,
        },
    }


@router.get("/pending-counts")
async def get_pending_counts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """KPI 卡片统计：待填采购单价/待传六面图/待传产品图/待审核（按当前用户可见范围）"""
    try:
        base = _apply_visibility_filter(db.query(AdProductInfo), db, current_user)
        review_pending = base.filter(
            AdProductInfo.designer_image.isnot(None),
            or_(AdProductInfo.image_review.is_(None), AdProductInfo.image_review == ""),
        )
        return {
            "success": True,
            "data": {
                "purchase": base.filter(AdProductInfo.purchase_cost.is_(None)).count(),
                "six_face": base.filter(AdProductInfo.six_face_image.is_(None)).count(),
                "designer": base.filter(AdProductInfo.designer_image.is_(None)).count(),
                "review": review_pending.count(),
            },
        }
    except Exception as e:
        logger.error(f"获取新品待办统计失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取新品待办统计失败: {str(e)}")


@router.get("/projects/{record_id}")
async def get_project_detail(
    record_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """查询单条开发新品记录（用于添加新品页仓库回填模式）"""
    query = _apply_visibility_filter(db.query(AdProductInfo), db, current_user)
    item = query.filter(AdProductInfo.record_id == record_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="记录不存在或无权查看")
    return {"success": True, "data": _serialize(item)}


class PurchasePayload(BaseModel):
    purchase_price: float
    purchased: Optional[str] = None  # 未采购/已采购
    purchase_note: Optional[str] = None  # 采购备注


@router.put("/projects/{record_id}/purchase")
async def update_purchase(
    record_id: str,
    payload: PurchasePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """采购部门填写采购单价、是否采购与采购备注"""
    item = _get_by_record_id(db, record_id, current_user.tenant_id)
    if not item:
        raise HTTPException(status_code=404, detail="记录不存在")
    item.purchase_cost = payload.purchase_price
    item.purchased = payload.purchased or "已采购"
    item.purchase_note = (payload.purchase_note or "").strip() or None
    db.commit()
    return {"success": True, "data": _serialize(item)}


class WarehousePayload(BaseModel):
    six_face_images: Optional[List[str]] = None
    warehouse_note: Optional[str] = None


@router.put("/projects/{record_id}/warehouse")
async def update_warehouse(
    record_id: str,
    payload: WarehousePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """仓库部门上传六面图并填写仓库备注"""
    item = _get_by_record_id(db, record_id, current_user.tenant_id)
    if not item:
        raise HTTPException(status_code=404, detail="记录不存在")
    if payload.six_face_images is not None:
        item.six_face_image = _images_to_blob(payload.six_face_images)
    if payload.warehouse_note is not None:
        item.warehouse_note = payload.warehouse_note
    db.commit()
    return {"success": True, "data": _serialize(item)}


class DesignerImagePayload(BaseModel):
    images: List[str]


@router.put("/projects/{record_id}/designer-image")
async def upload_designer_image(
    record_id: str,
    payload: DesignerImagePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """美工上传产品图片：自动生成完成图片日期，并推送管理层审核"""
    item = _get_by_record_id(db, record_id, current_user.tenant_id)
    if not item:
        raise HTTPException(status_code=404, detail="记录不存在")
    item.designer_image = _images_to_blob(payload.images)
    item.designer_complete_date = date.today().strftime("%Y-%m-%d")
    if not item.designer_assignee:
        item.designer_assignee = current_user.nickname or current_user.username
    db.commit()
    name = item.product_name_zh or item.title_en or record_id
    _notify_department_users(
        db, current_user.tenant_id, "管理层",
        "新品图片审核提醒",
        f"美工已上传新品「{name}」的产品图片，请前往开发机器人审核。",
        f"/development?record={record_id}&tab=review",
        ntype="development_review",
    )
    db.commit()
    return {"success": True, "data": _serialize(item)}


class ReviewPayload(BaseModel):
    approved: bool


@router.put("/projects/{record_id}/review")
async def review_designer_image(
    record_id: str,
    payload: ReviewPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """管理层审核产品图片，通过后开发机器人首页展示产品图片"""
    item = _get_by_record_id(db, record_id, current_user.tenant_id)
    if not item:
        raise HTTPException(status_code=404, detail="记录不存在")
    item.image_review = "已通过" if payload.approved else "不通过请看评论"
    if payload.approved:
        # 审核通过：将美工产品图片同步到产品管理对应商品的产品图片
        try:
            _sync_designer_images_to_product(db, item)
        except Exception as e:
            logger.warning(f"同步产品图片到产品管理失败（不阻塞审核）: {e}")
    db.commit()
    return {"success": True, "data": _serialize(item)}


# ==================== 勾选生成补货单（进销存-补货管理） ====================

class DevReplenishmentItemPayload(BaseModel):
    record_id: str
    quantity: int = 100
    notes: Optional[str] = None


class DevReplenishmentPayload(BaseModel):
    store_group_id: Optional[int] = None
    notes: Optional[str] = None
    items: List[DevReplenishmentItemPayload]


def _resolve_product_id(db: Session, tenant_id: int, item: AdProductInfo) -> int:
    """按产品编码找 products 记录；没有则自动建一条基础商品档案"""
    if not item.product_code:
        item.product_code = _match_or_create_product_code(db, tenant_id, item) or _next_product_code(db, tenant_id)
        db.flush()
    row = db.execute(text(
        "SELECT id FROM products WHERE tenant_id = :tid AND product_code = :code AND deleted_at IS NULL ORDER BY id LIMIT 1"
    ), {"tid": tenant_id, "code": item.product_code}).fetchone()
    if row:
        return row[0]
    db.execute(text("""
        INSERT INTO products (tenant_id, product_code, name, name_en, category, purchase_price, status,
            is_robot_monitored, no_accessory, product_attribute, created_at, updated_at)
        VALUES (:tid, :code, :name, :name_en, :category, :price, 'active', 1, 0, 'general', NOW(), NOW())
    """), {
        "tid": tenant_id,
        "code": item.product_code,
        "name": item.product_name_zh or item.title_en or item.product_code,
        "name_en": item.title_en,
        "category": item.category,
        "price": item.purchase_cost,
    })
    return db.execute(text("SELECT LAST_INSERT_ID()")).scalar()


@router.post("/projects/replenishment")
async def create_replenishment_from_dev(
    payload: DevReplenishmentPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """开发机器人勾选商品批量生成进销存补货单（状态 pending，等待审批）"""
    if not payload.items:
        raise HTTPException(status_code=400, detail="请至少勾选一条新品")
    try:
        store_group_name = ""
        if payload.store_group_id:
            group = db.execute(text(
                "SELECT name FROM store_groups WHERE id = :gid AND deleted_at IS NULL"
            ), {"gid": payload.store_group_id}).fetchone()
            if not group:
                raise HTTPException(status_code=400, detail=f"店铺分组 {payload.store_group_id} 不存在")
            store_group_name = group[0]

        order_number = f"RO{datetime.now().strftime('%Y%m%d%H%M%S')}{random.randint(100, 999)}"
        db.execute(text("""
            INSERT INTO replenishment_orders (tenant_id, order_number, store_group_id, status, notes,
                created_by, created_at, updated_at)
            VALUES (:tid, :num, :gid, 'pending', :notes, :uid, NOW(), NOW())
        """), {"tid": current_user.tenant_id, "num": order_number, "gid": payload.store_group_id,
               "notes": payload.notes, "uid": current_user.id})
        order_id = db.execute(text("SELECT LAST_INSERT_ID()")).scalar()

        count = 0
        for it in payload.items:
            product = _get_by_record_id(db, it.record_id, current_user.tenant_id)
            if not product:
                continue
            product_id = _resolve_product_id(db, current_user.tenant_id, product)
            db.execute(text("""
                INSERT INTO replenishment_items (tenant_id, replenishment_order_id, product_id, quantity,
                    notes, created_at, updated_at)
                VALUES (:tid, :oid, :pid, :qty, :notes, NOW(), NOW())
            """), {"tid": current_user.tenant_id, "oid": order_id, "pid": product_id,
                   "qty": max(1, it.quantity), "notes": it.notes})
            count += 1
        if count == 0:
            db.rollback()
            raise HTTPException(status_code=400, detail="勾选的新品记录均不存在")
        db.commit()
        return {"success": True, "data": {"id": order_id, "order_number": order_number, "item_count": count,
                                          "store_group_name": store_group_name}}
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"开发机器人生成补货单失败: {e}")
        raise HTTPException(status_code=500, detail=f"生成补货单失败: {str(e)}")


@router.post("/projects/{record_id}/purchase-order")
async def create_purchase_order_from_dev(
    record_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """点击「采购」按钮：按店铺分组采购数量在进销存采购管理创建采购单（状态 draft）。
    明细：店铺分组 = 添加新品选择的分组，数量 = 采购数量，备注 = 采购备注（仓库备注）"""
    item = _get_by_record_id(db, record_id, current_user.tenant_id)
    if not item:
        raise HTTPException(status_code=404, detail="记录不存在")
    if (item.purchased or "") == "已生成采购单":
        raise HTTPException(status_code=400, detail="该新品已生成采购单，请勿重复操作")
    try:
        product_id = _resolve_product_id(db, current_user.tenant_id, item)
        quantities = _parse_purchase_items(item.store_quantity_json)
        notes = (item.warehouse_note or "").strip() or None
        order_number = f"PO{datetime.now().strftime('%Y%m%d%H%M%S')}{random.randint(100, 999)}"
        unit_price = float(item.purchase_cost or 0)
        db.execute(text("""
            INSERT INTO purchase_orders (tenant_id, order_number, total_amount, status, notes, created_by, created_at, updated_at)
            VALUES (:tid, :num, 0, 'draft', :notes, :uid, NOW(), NOW())
        """), {"tid": current_user.tenant_id, "num": order_number, "notes": notes, "uid": current_user.id})
        order_id = db.execute(text("SELECT LAST_INSERT_ID()")).scalar()

        count = 0
        for q in quantities:
            store = str(q.get("store") or "").strip()
            try:
                qty = int(q.get("quantity") or 0)
            except (TypeError, ValueError):
                qty = 0
            if qty <= 0:
                continue
            gid_row = db.execute(text(
                "SELECT id FROM store_groups WHERE tenant_id = :tid AND name = :name AND deleted_at IS NULL ORDER BY id LIMIT 1"
            ), {"tid": current_user.tenant_id, "name": store}).fetchone()
            db.execute(text("""
                INSERT INTO purchase_order_items (purchase_order_id, product_id, quantity, unit_price, total_price,
                    notes, store_group_id, created_at, updated_at)
                VALUES (:oid, :pid, :qty, :price, :total, :notes, :gid, NOW(), NOW())
            """), {"oid": order_id, "pid": product_id, "qty": qty, "price": unit_price,
                   "total": qty * unit_price, "notes": notes, "gid": gid_row[0] if gid_row else None})
            count += 1
        if count == 0:
            db.rollback()
            raise HTTPException(status_code=400, detail="该新品未填写店铺分组与采购数量，无法生成采购单")

        item.purchased = "已生成采购单"
        db.commit()
        return {"success": True, "data": {"order_id": order_id, "order_number": order_number, "item_count": count}}
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"开发机器人生成采购单失败: {e}")
        raise HTTPException(status_code=500, detail=f"生成采购单失败: {str(e)}")


# ==================== 飞书文档预览 ====================

import re
from urllib.parse import unquote

import httpx

from services.feishu_service import get_access_token

FEISHU_API_BASE = "https://open.feishu.cn/open-apis"


def _parse_feishu_url(url: str) -> tuple[str, str]:
    """从飞书链接中提取 (类型, token)。支持 wiki / docx / docs 链接"""
    url = url.strip()
    m = re.search(r"/wiki/([A-Za-z0-9]+)", url)
    if m:
        return "wiki", m.group(1)
    m = re.search(r"/docx/([A-Za-z0-9]+)", url)
    if m:
        return "docx", m.group(1)
    m = re.search(r"/docs/([A-Za-z0-9]+)", url)
    if m:
        return "docs", m.group(1)
    return "", ""


# 明显采购链接域名：文档预览中仅采购部（及管理员）可见，其他部门屏蔽
_PURCHASE_DOMAINS = (
    "1688.com", "taobao.com", "tmall.com", "alibaba.com", "aliexpress.com",
    "dhgate.com", "made-in-china.com", "globalsources.com", "pinduoduo.com",
    "yangkeduo.com",
)

# 匹配文本中的 URL（http/www 开头）或不带协议的采购域名链接
# 排除字符集含全角括号（）、书名号等中文标点，避免 URL 尾部粘连中文说明
_PURCHASE_URL_RE = re.compile(
    r"(?:https?://|www\.)[^\s，。、；！？,;；\"'）)】\]（《〉]+"
    r"|(?:[A-Za-z0-9-]+\.)*(?:1688|taobao|tmall|alibaba|aliexpress|dhgate|made-in-china|globalsources|pinduoduo|yangkeduo)\.com(?:/[^\s，。、；！？,;；\"'）)】\]（《〉]*)?",
    re.IGNORECASE,
)

_PURCHASE_LINK_MASK = "【采购链接，仅采购部可见】"


def _mask_purchase_links(content: str) -> tuple[str, int]:
    """屏蔽文本中的采购链接，返回 (屏蔽后文本, 屏蔽数量)"""
    count = 0

    def _repl(m: "re.Match") -> str:
        nonlocal count
        seg = m.group(0)
        if any(d in seg.lower() for d in _PURCHASE_DOMAINS):
            count += 1
            return _PURCHASE_LINK_MASK
        return seg

    return _PURCHASE_URL_RE.sub(_repl, content), count


# docx 块类型 -> 该块存放文本 elements 的字段名
_DOC_TEXT_FIELDS = {2: "text", 12: "bullet", 13: "ordered", 14: "code", 15: "quote", 17: "todo"}


async def _fetch_doc_elements(client: "httpx.AsyncClient", headers: dict, doc_token: str) -> List[dict]:
    """拉取文档块结构（分页遍历全部块），按顺序输出 [{type:'text',content}| {type:'image',file_token}]"""
    items: List[dict] = []
    page_token = ""
    while True:
        params: dict = {"page_size": 500}
        if page_token:
            params["page_token"] = page_token
        resp = await client.get(
            f"{FEISHU_API_BASE}/docx/v1/documents/{doc_token}/blocks",
            params=params,
            headers=headers,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise HTTPException(status_code=502, detail=f"读取飞书文档结构失败: {data.get('msg')}")
        payload = data.get("data", {})
        items.extend(payload.get("items") or [])
        page_token = payload.get("page_token") or ""
        if not payload.get("has_more"):
            break
    block_map = {b.get("block_id"): b for b in items}

    elements: List[dict] = []

    def walk(block_id: str, depth: int):
        block = block_map.get(block_id)
        if not block or depth > 8:  # 表格/分组嵌套较深，放宽深度限制
            return
        bt = block.get("block_type")
        if bt == 27:  # 图片块
            image_token = (block.get("image") or {}).get("token")
            if image_token:
                elements.append({"type": "image", "file_token": image_token})
        elif bt in _DOC_TEXT_FIELDS:
            text = ""
            for el in (block.get(_DOC_TEXT_FIELDS[bt]) or {}).get("elements", []) or []:
                run = el.get("text_run") or {}
                run_text = run.get("content", "") or ""
                # 超链接样式的文本块：把真实链接 URL 附加到文本（采购部可见链接目标）
                link_url = ((run.get("text_element_style") or {}).get("link") or {}).get("url") or ""
                if link_url:
                    link_url = unquote(link_url)
                    if link_url not in run_text:
                        run_text = f"{run_text}（{link_url}）" if run_text.strip() else link_url
                text += run_text
            if text.strip():
                elements.append({"type": "text", "content": text.strip()})
        for child in block.get("children") or []:
            walk(child, depth + 1)

    # 根块 block_id 与 document_id 相同；找不到时退化为顶层块（parent 为文档 ID）
    if doc_token in block_map:
        walk(doc_token, 0)
    else:
        for b in items:
            if b.get("parent_id") == doc_token:
                walk(b.get("block_id"), 1)
    return elements


@router.get("/projects/{record_id}/doc")
async def get_project_doc(
    record_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """读取记录对应飞书文档的内容（文本+图片按文档顺序，用于卡片悬停预览）"""
    try:
        item = _get_by_record_id(db, record_id, current_user.tenant_id)
        if not item:
            raise HTTPException(status_code=404, detail="记录不存在")
        if not item.doc_link:
            return {"success": True, "data": {"doc_name": item.doc_name, "doc_link": None, "elements": [], "content": "", "message": "该记录没有文档链接"}}

        doc_type, token = _parse_feishu_url(item.doc_link)
        if not token:
            return {"success": True, "data": {"doc_name": item.doc_name, "doc_link": item.doc_link, "elements": [], "content": "", "message": "无法识别的文档链接格式"}}

        headers = {"Authorization": f"Bearer {await get_access_token()}"}
        async with httpx.AsyncClient(timeout=20) as client:
            # wiki 链接需要先解析为真实文档 token
            if doc_type == "wiki":
                resp = await client.get(
                    f"{FEISHU_API_BASE}/wiki/v2/spaces/get_node",
                    params={"token": token},
                    headers=headers,
                )
                data = resp.json()
                if data.get("code") != 0:
                    raise HTTPException(status_code=502, detail=f"解析飞书文档节点失败: {data.get('msg')}")
                node = data.get("data", {}).get("node", {})
                obj_type = node.get("obj_type", "")
                obj_token = node.get("obj_token", "")
                if obj_type != "docx":
                    return {"success": True, "data": {"doc_name": item.doc_name, "doc_link": item.doc_link, "elements": [], "content": "", "message": f"暂不支持预览 {obj_type or '未知'} 类型文档，请点击打开"}}
                token = obj_token

            elements = await _fetch_doc_elements(client, headers, token)

        # 采购链接权限：1688 等明显采购链接仅采购部（及管理员）可见，其他部门屏蔽
        can_view_purchase_links = _is_admin_user(db, current_user) or "采购部" in get_user_department_names(db, current_user)
        links_hidden = 0
        if not can_view_purchase_links:
            for el in elements:
                if el["type"] == "text":
                    el["content"], n = _mask_purchase_links(el["content"])
                    links_hidden += n

        content = "\n".join([e["content"] for e in elements if e["type"] == "text"])
        return {
            "success": True,
            "data": {
                "doc_name": item.doc_name,
                "doc_link": item.doc_link,
                "elements": elements,
                "content": content,
                "links_hidden": links_hidden,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"读取飞书文档失败: {e}")
        raise HTTPException(status_code=500, detail=f"读取飞书文档失败: {str(e)}")


@router.get("/doc-image/{file_token}")
async def get_doc_image(file_token: str):
    """飞书文档图片代理：后端持 token 下载图片并转发（file_token 不可枚举，故不做用户鉴权，供 <img> 标签直接引用）"""
    try:
        headers = {"Authorization": f"Bearer {await get_access_token()}"}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{FEISHU_API_BASE}/drive/v1/medias/{file_token}/download",
                headers=headers,
            )
        if resp.status_code != 200:
            try:
                detail = resp.json().get("msg") or f"HTTP {resp.status_code}"
            except Exception:
                detail = f"HTTP {resp.status_code}"
            raise HTTPException(status_code=502, detail=f"下载文档图片失败: {detail}")
        media_type = (resp.headers.get("content-type") or "image/jpeg").split(";")[0]
        if not media_type.startswith("image/"):
            media_type = "image/jpeg"
        return Response(content=resp.content, media_type=media_type)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"下载文档图片失败: {e}")
        raise HTTPException(status_code=500, detail=f"下载文档图片失败: {str(e)}")


@router.get("/attachment-image/{table_id}/{file_token}")
async def get_attachment_image(table_id: str, file_token: str):
    """飞书多维表附件图片代理：附件下载需带 extra(bitablePerm) 参数，后端持 token 下载转发
    （file_token 不可枚举，与 doc-image 同安全级别，供 <img> 标签直接引用）"""
    import json as _json
    if not re.match(r"^tbl[a-zA-Z0-9]+$", table_id) or not re.match(r"^[a-zA-Z0-9]+$", file_token):
        raise HTTPException(status_code=400, detail="参数格式非法")
    try:
        headers = {"Authorization": f"Bearer {await get_access_token()}"}
        extra = _json.dumps({"bitablePerm": {"tableId": table_id}})
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(
                f"{FEISHU_API_BASE}/drive/v1/medias/{file_token}/download",
                headers=headers,
                params={"extra": extra},
            )
        if resp.status_code != 200:
            try:
                detail = resp.json().get("msg") or f"HTTP {resp.status_code}"
            except Exception:
                detail = f"HTTP {resp.status_code}"
            raise HTTPException(status_code=502, detail=f"下载附件图片失败: {detail}")
        media_type = (resp.headers.get("content-type") or "image/jpeg").split(";")[0]
        if not media_type.startswith("image/"):
            media_type = "image/jpeg"
        return Response(content=resp.content, media_type=media_type)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"下载附件图片失败: {e}")
        raise HTTPException(status_code=500, detail=f"下载附件图片失败: {str(e)}")


# ==================== 飞书多维表回传 / 美工图片迁移 TOS ====================

# 开发新品多维表（配置从 .env 读取，避免在业务代码中暴露）
from config import get_settings as _get_settings

_settings_dev = _get_settings()
FEISHU_BASE_TOKEN = _settings_dev.FEISHU_DESIGN_BASE_TOKEN
FEISHU_DESIGN_TABLE_ID = _settings_dev.FEISHU_DESIGN_TABLE_ID

# 回传时跳过的数据库列（主键/系统列/二进制列单独处理）
_PUSH_SKIP_COLUMNS = {
    "id", "tenant_id", "record_id", "created_at", "updated_at", "created_by", "updated_by",
    "purchase_images", "six_face_image", "designer_image", "purchase_items_json", "store_quantity_json",
}


def _norm_field_name(name: str) -> str:
    """字段名归一化：去掉尾部的（...）/(...) 补充说明"""
    return re.sub(r"[（(].*?[）)]\s*$", "", (name or "").strip()).strip()


def _coerce_field_value(col_type, value, field_type):
    """按飞书字段类型把数据库值转成可写入的载荷；不支持时返回 None"""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    if value is None or str(value).strip() == "":
        return None
    try:
        if field_type == 1:      # 多行文本
            return str(value)
        if field_type == 2:      # 数字
            return float(value)
        if field_type == 3:      # 单选
            return str(value)
        if field_type == 4:      # 多选
            return [p for p in re.split(r"[,，、\n]", str(value)) if p.strip()]
        if field_type == 5:      # 日期（毫秒时间戳）
            return int(datetime.strptime(str(value)[:10], "%Y-%m-%d").timestamp() * 1000)
    except Exception:
        return None
    return None


async def _upload_designer_images_to_record(
    client: "httpx.AsyncClient", headers: dict, base_url: str,
    record_id: str, field_name: str, images: List[str], name_hint: str,
) -> int:
    """把美工图片（TOS/代理 URL）下载后作为附件上传到多维表记录，返回成功张数"""
    uploaded, file_tokens = 0, []
    for i, raw in enumerate(images[:9]):
        url = raw.strip()
        if not url:
            continue
        if not url.startswith("http"):
            url = f"http://127.0.0.1:8002{url}"
        try:
            img_resp = await client.get(url, headers=headers, timeout=60)
            img_resp.raise_for_status()
            content = img_resp.content
            ext = ".png" if "image/png" in (img_resp.headers.get("content-type") or "") else ".jpg"
            safe_name = re.sub(r"[\\/:*?\"<>|\s,，;；]", "", name_hint)[:40] or record_id
            file_name = f"{safe_name}_{i + 1}{ext}"
            media = await client.post(
                f"{base_url}/records/{record_id}/files/upload",
                headers={"Authorization": headers["Authorization"]},
                data={"file_name": file_name, "parent_type": "bitable_image", "size": str(len(content))},
                files={"file": (file_name, content, "image/jpeg")},
                timeout=120,
            )
            mdata = media.json()
            ft = (mdata.get("data", {}) or {}).get("file_token")
            if mdata.get("code") == 0 and ft:
                file_tokens.append(ft)
                uploaded += 1
            else:
                logger.warning(f"附件上传失败({name_hint}#{i + 1}): {mdata.get('msg')}")
        except Exception as e:
            logger.warning(f"美工图片下载/上传失败({name_hint}#{i + 1}): {e}")
    if file_tokens:
        try:
            await client.patch(
                f"{base_url}/records/{record_id}",
                headers=headers,
                json={"fields": {field_name: [{"file_token": t} for t in file_tokens]}},
            )
        except Exception as e:
            logger.warning(f"绑定附件字段失败: {e}")
            uploaded = 0
    return uploaded


@router.post("/projects/{record_id}/push-to-feishu")
async def push_project_to_feishu(
    record_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """回传飞书：把开发新品记录写入对应多维表。
    - 数据库记录 ID 在多维表中存在 → 按原记录修改
    - 不存在 → 新增一条记录，并把多维表返回的记录 ID 回传到数据库
    - 字段按数据库列备注与多维表字段名（去掉括号补充后）精确匹配填写
    - 美工图片作为附件写入「美工图片/产品图片」附件字段"""
    from sqlalchemy.exc import IntegrityError

    item = _get_by_record_id(db, record_id, current_user.tenant_id)
    if not item:
        raise HTTPException(status_code=404, detail="记录不存在")
    try:
        token = await get_access_token()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"获取飞书凭证失败: {e}")
    auth = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    base = f"{FEISHU_API_BASE}/bitable/v1/apps/{FEISHU_BASE_TOKEN}/tables/{FEISHU_DESIGN_TABLE_ID}"

    async with httpx.AsyncClient(timeout=60) as client:
        # 1. 拉取多维表字段定义
        fresp = await client.get(f"{base}/fields?page_size=100", headers=auth)
        fdata = fresp.json()
        if fdata.get("code") != 0:
            raise HTTPException(status_code=502, detail=f"获取多维表字段失败: {fdata.get('msg')}")
        fields = fdata.get("data", {}).get("items", [])

        # 2. 数据库列备注 -> 字段名 匹配
        col_by_name = {}
        for col in AdProductInfo.__table__.columns:
            if col.name in _PUSH_SKIP_COLUMNS:
                continue
            norm = _norm_field_name(col.comment or "")
            if norm:
                col_by_name[norm] = col
        fields_payload: dict = {}
        field_meta = {}
        for f in fields:
            fname = f.get("field_name") or ""
            col = col_by_name.get(_norm_field_name(fname))
            if col is None:
                continue
            value = _coerce_field_value(col.type, getattr(item, col.name), f.get("type"))
            if value is None:
                continue
            fields_payload[fname] = value
            field_meta[fname] = f

        # 3. 按记录 ID 判断新增/修改
        rresp = await client.get(f"{base}/records/{item.record_id}", headers=auth)
        exists = rresp.status_code == 200 and (rresp.json().get("data", {}) or {}).get("record")
        if exists:
            uresp = await client.patch(f"{base}/records/{item.record_id}", headers=auth, json={"fields": fields_payload})
            udata = uresp.json()
            if udata.get("code") != 0:
                raise HTTPException(status_code=502, detail=f"更新飞书记录失败: {udata.get('msg')}")
            feishu_rid = item.record_id
            mode = "updated"
        else:
            cres = await client.post(f"{base}/records", headers=auth, json={"fields": fields_payload})
            cdata = cres.json()
            if cdata.get("code") != 0:
                raise HTTPException(status_code=502, detail=f"创建飞书记录失败: {cdata.get('msg')}")
            feishu_rid = (cdata.get("data", {}).get("record", {}) or {}).get("record_id") or item.record_id
            mode = "created"

        # 4. 「记录 ID」字段回写多维表记录 ID
        rid_field = next(
            (f.get("field_name") for f in fields if _norm_field_name(f.get("field_name") or "").lower() == "记录id"),
            None,
        )
        if rid_field:
            try:
                await client.patch(f"{base}/records/{feishu_rid}", headers=auth,
                                   json={"fields": {rid_field: feishu_rid}})
            except Exception as e:
                logger.warning(f"回写记录ID字段失败: {e}")

        # 5. 美工图片附件写入
        att_field = next(
            (f for f in fields if f.get("type") == 17 and ("美工" in (f.get("field_name") or "") or "产品图片" in (f.get("field_name") or ""))),
            None,
        )
        attached = 0
        if att_field:
            images = _blob_to_images(item.designer_image)
            if images:
                attached = await _upload_designer_images_to_record(
                    client, auth, base, feishu_rid, att_field["field_name"], images,
                    item.product_name_zh or item.record_id,
                )

    # 6. 多维表记录 ID 回传数据库
    rid_changed = feishu_rid != item.record_id
    if rid_changed:
        old_rid = item.record_id
        item.record_id = feishu_rid
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            item.record_id = old_rid
            db.commit()
            logger.warning(f"记录ID {feishu_rid} 已存在，保留原ID {old_rid}")
            rid_changed = False

    return {
        "success": True,
        "data": {
            "mode": mode,
            "record_id": feishu_rid,
            "record_id_changed": rid_changed,
            "fields_filled": len(fields_payload),
            "images_attached": attached,
        },
    }


_MIGRATE_RUNNING: dict = {}  # 按迁移列记录运行状态，避免美工图/六面图任务互斥


def _safe_image_name(name: str) -> str:
    """文件名安全化（清洗非法字符）"""
    return re.sub(r"[\\/:*?\"<>|\s,，;；]", "", name or "").strip()[:40]


async def _migrate_designer_batch(batch: int = 100, column: str = "designer_image", name_prefix: str = "新品") -> dict:
    """把多维表同步来的图片（代理 URL）批量转存到 TOS（并发加速版）。
    column 支持 designer_image（美工图）与 six_face_image（六面图）；
    文件名格式：{前缀}_中文产品名_日期_序号"""
    from database.database import SessionLocal
    from services.tos_service import upload_image
    import httpx as _httpx

    if column not in ("designer_image", "six_face_image"):
        raise ValueError(f"不支持的迁移列: {column}")

    db = SessionLocal()
    try:
        remaining_q = text(
            f"SELECT COUNT(*) FROM ad_product_info WHERE {column} IS NOT NULL AND {column} LIKE '%attachment-image%'"
        )
        total_remaining = db.execute(remaining_q).scalar() or 0
        if total_remaining == 0:
            return {"remaining": 0, "scanned": 0, "migrated": 0, "failed": 0, "done": True}

        rows = db.execute(text(
            f"SELECT id, record_id, product_name_zh, purchase_date, {column} FROM ad_product_info "
            f"WHERE {column} IS NOT NULL AND {column} LIKE '%attachment-image%' LIMIT :lim"
        ), {"lim": batch}).fetchall()

        token_holder = {"token": None}
        sem = asyncio.Semaphore(8)  # 最多 8 张图片并发下载/上传

        async def migrate_one(table_id: str, file_token: str, custom_name: str) -> Optional[str]:
            """下载单张飞书附件并上传 TOS（在线程池执行阻塞上传），失败返回 None"""
            async with sem:
                try:
                    if token_holder["token"] is None:
                        token_holder["token"] = await get_access_token()
                    headers = {"Authorization": f"Bearer {token_holder['token']}"}
                    extra = json.dumps({"bitablePerm": {"tableId": table_id}})
                    resp = await client.get(
                        f"{FEISHU_API_BASE}/drive/v1/medias/{file_token}/download",
                        headers=headers, params={"extra": extra},
                    )
                    if resp.status_code != 200:
                        return None
                    return await asyncio.to_thread(
                        upload_image, resp.content, f"{file_token}.jpg", custom_name
                    )
                except Exception as e:
                    logger.warning(f"美工图迁移单张失败 {file_token}: {e}")
                    return None

        scanned = migrated = failed = 0

        async def migrate_record(row) -> tuple:
            """处理一条记录的全部代理图片，返回 (pid, new_urls, changed, migrated, failed)"""
            pid, rid, name_zh, purchase_date, blob = row
            try:
                if isinstance(blob, bytes):
                    blob = blob.decode("utf-8", errors="ignore")
                urls = [u.strip() for u in str(blob).split(",") if u.strip()]
            except Exception:
                return pid, None, False, 0, 0
            date_part = str(purchase_date or "").replace("-", "")[:8] or datetime.now().strftime("%Y%m%d")
            safe = _safe_image_name(name_zh) or rid
            tasks, proxy_index = [], []
            for i, u in enumerate(urls):
                m = re.search(r"/attachment-image/(tbl[A-Za-z0-9]+)/([A-Za-z0-9]+)", u)
                if m:
                    tasks.append(migrate_one(m.group(1), m.group(2), f"{name_prefix}_{safe}_{date_part}_{i + 1}"))
                    proxy_index.append(i)
                # 非代理 URL 直接保留
            results = await asyncio.gather(*tasks) if tasks else []
            new_urls = list(urls)
            rec_migrated = rec_failed = 0
            for idx, new_url in zip(proxy_index, results):
                if new_url:
                    new_urls[idx] = new_url
                    rec_migrated += 1
                else:
                    rec_failed += 1
            return pid, new_urls, rec_migrated > 0, rec_migrated, rec_failed

        async with _httpx.AsyncClient(timeout=60) as client:
            results = await asyncio.gather(*(migrate_record(row) for row in rows))

        for (pid, new_urls, changed, rec_migrated, rec_failed), row in zip(results, rows):
            scanned += 1
            migrated += rec_migrated
            failed += rec_failed
            if changed and new_urls:
                db.execute(text(f"UPDATE ad_product_info SET {column} = :img WHERE id = :pid"),
                           {"img": ",".join(new_urls), "pid": pid})
                db.commit()

        remaining = db.execute(remaining_q).scalar() or 0
        return {"remaining": remaining, "scanned": scanned, "migrated": migrated, "failed": failed, "done": remaining == 0}
    finally:
        db.close()


async def auto_migrate_designer_images(column: str = "designer_image", name_prefix: str = "新品", env_key: str = "DESIGNER_IMAGE_AUTO_MIGRATE", label: str = "美工图片"):
    """服务启动后自动后台迁移多维表图片到 TOS，循环执行直到全部完成。

    可通过环境变量（env_key）=false 关闭；连续两批无进展时自动停止。
    """
    global _MIGRATE_RUNNING
    import os
    if _MIGRATE_RUNNING.get(column):
        return
    from config import get_settings
    if getattr(get_settings(), env_key, "true").strip().lower() in ("false", "0", "no"):
        logger.info(f"{label} TOS 自动迁移已通过环境变量关闭")
        return
    _MIGRATE_RUNNING[column] = True
    try:
        await asyncio.sleep(5)  # 等服务与 DB 就绪
        no_progress_rounds = 0
        while True:
            try:
                result = await _migrate_designer_batch(100, column=column, name_prefix=name_prefix)
            except Exception as e:
                logger.warning(f"{label}自动迁移异常，60 秒后重试: {e}")
                await asyncio.sleep(60)
                continue
            if result.get("done"):
                logger.info(f"{label}已全部迁移到 TOS（最后一批迁移 {result.get('migrated', 0)} 张，失败 {result.get('failed', 0)} 张）")
                break
            if result.get("migrated", 0) == 0:
                no_progress_rounds += 1
                if no_progress_rounds >= 2:
                    logger.warning(f"{label}迁移连续 {no_progress_rounds} 批无进展，剩余 {result.get('remaining')} 条（均为无法下载的失效附件），自动任务已停止；可查看失败链接或手动处理")
                    break
            else:
                no_progress_rounds = 0
            logger.info(f"{label}自动迁移进行中：剩余 {result.get('remaining')} 条")
            await asyncio.sleep(1)
    finally:
        _MIGRATE_RUNNING[column] = False


async def auto_migrate_sixface_images():
    """六面图 TOS 自动迁移（复用美工图迁移逻辑，列=six_face_image）"""
    await auto_migrate_designer_images(
        column="six_face_image", name_prefix="六面图",
        env_key="SIXFACE_IMAGE_AUTO_MIGRATE", label="六面图",
    )


@router.post("/designer-images/migrate-to-tos")
async def migrate_designer_images_to_tos(
    batch: int = Query(100, ge=1, le=500, description="每批处理的记录数"),
    current_user: User = Depends(get_current_admin_user),
):
    """手动触发一批美工图片迁移（自动迁移已在服务启动时后台执行）"""
    return {"success": True, "data": await _migrate_designer_batch(batch)}


@router.get("/designer-images/migrate-status")
async def designer_migrate_status(current_user: User = Depends(get_current_admin_user)):
    """美工图片 TOS 迁移进度（管理员）：剩余未迁移 / 总记录数"""
    from database.database import SessionLocal
    db = SessionLocal()
    try:
        row = db.execute(text(
            "SELECT COUNT(*) AS total, "
            "SUM(designer_image LIKE '%attachment-image%') AS remaining, "
            "SUM(designer_image LIKE '%tos-%') AS migrated "
            "FROM ad_product_info WHERE designer_image IS NOT NULL AND designer_image != ''"
        )).fetchone()
        total = int(row[0] or 0)
        remaining = int(row[1] or 0)
        migrated = int(row[2] or 0)
        return {
            "success": True,
            "data": {
                "total": total,
                "remaining": remaining,
                "migrated": migrated,
                "done": remaining == 0,
            },
        }
    finally:
        db.close()


@router.post("/six-face-images/migrate-to-tos")
async def migrate_sixface_images_to_tos(
    batch: int = Query(100, ge=1, le=500, description="每批处理的记录数"),
    current_user: User = Depends(get_current_admin_user),
):
    """手动触发一批六面图迁移（自动迁移已在服务启动时后台执行）"""
    return {"success": True, "data": await _migrate_designer_batch(batch, column="six_face_image", name_prefix="六面图")}


@router.get("/six-face-images/migrate-status")
async def sixface_migrate_status(current_user: User = Depends(get_current_admin_user)):
    """六面图 TOS 迁移进度（管理员）：剩余未迁移 / 总记录数"""
    from database.database import SessionLocal
    db = SessionLocal()
    try:
        row = db.execute(text(
            "SELECT COUNT(*) AS total, "
            "SUM(six_face_image LIKE '%attachment-image%') AS remaining, "
            "SUM(six_face_image LIKE '%tos-%') AS migrated "
            "FROM ad_product_info WHERE six_face_image IS NOT NULL AND six_face_image != ''"
        )).fetchone()
        total = int(row[0] or 0)
        remaining = int(row[1] or 0)
        migrated = int(row[2] or 0)
        return {
            "success": True,
            "data": {
                "total": total,
                "remaining": remaining,
                "migrated": migrated,
                "done": remaining == 0,
            },
        }
    finally:
        db.close()
