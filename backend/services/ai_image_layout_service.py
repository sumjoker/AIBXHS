"""A+ 版式参考服务

内置版式来自 backend/prompts/aplus_layouts.json（运营可直接改，不用改代码）；
用户上传 / 收藏落 ai_image_layouts 表。

对齐的交互（参考 ai.linkfox.com 商品套图）：
- 官方推荐 / 我的 / 收藏 三个来源
- 最多选 8 个版式，每种版式生成一张
- 未选版式时随机取一个官方版式（不退回「无版式自由发挥」）
"""
from __future__ import annotations

import json
import logging
import os
import random
import threading
from typing import Any, Dict, List, Optional

from config import get_settings

logger = logging.getLogger(__name__)

_PRESET_CACHE: Dict[str, Any] = {"mtime": 0.0, "data": None}
_preset_lock = threading.Lock()

RATIO_TAGS = ("horizontal", "vertical", "square")
RATIO_TAG_LABELS = {"horizontal": "横版", "vertical": "竖版", "square": "方版"}
# ratio_tag 只表达「横/竖/方」；用于兼容性判断的参考比例（非精确画布）
RATIO_TAG_ASPECT = {"horizontal": 16 / 9, "vertical": 9 / 16, "square": 1.0}
# A+ 目标画布比例（与 APLUS_SPECS.target_size 一致）
APLUS_TARGET_ASPECT = {
    "aplus_advanced": 1464 / 600,
    "aplus_normal": 970 / 600,
    "aplus_mobile": 600 / 450,
}


class LayoutError(Exception):
    """版式相关业务错误（路由层转 400）"""


# ============================================================
# 内置预设
# ============================================================

def load_presets() -> List[Dict[str, Any]]:
    """读取内置版式预设（按 mtime 缓存，改 JSON 后无需重启）"""
    path = get_settings().IMAGE_LAYOUT_PRESETS_PATH
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        raise LayoutError(f"版式预设文件不存在: {path}")
    with _preset_lock:
        if _PRESET_CACHE["data"] is not None and _PRESET_CACHE["mtime"] == mtime:
            return _PRESET_CACHE["data"]
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as e:
            raise LayoutError(f"读取版式预设失败: {e}")
        items = raw.get("presets") or []
        version = str(raw.get("version") or 1)
        cleaned: List[Dict[str, Any]] = []
        for it in items:
            pid = str(it.get("id") or "").strip()
            if not pid:
                continue
            ratio_tag = it.get("ratio_tag") if it.get("ratio_tag") in RATIO_TAGS else "horizontal"
            cleaned.append({
                "key": f"preset:{pid}",
                "id": pid,
                "name": it.get("name") or pid,
                "desc": it.get("desc") or "",
                "ratio_tag": ratio_tag,
                "aspect_ratio": RATIO_TAG_ASPECT.get(ratio_tag),
                "aspect_source": "ratio_tag",
                "template_version": version,
                "structure": it.get("structure") or "",
                "zones": it.get("zones") or [],
                "source": "preset",
            })
        _PRESET_CACHE["data"] = cleaned
        _PRESET_CACHE["mtime"] = mtime
        return cleaned


def get_preset(preset_id: str) -> Optional[Dict[str, Any]]:
    for p in load_presets():
        if p["id"] == preset_id:
            return p
    return None


def random_preset(ratio_tag: str = "horizontal") -> Optional[Dict[str, Any]]:
    """未选版式时的兜底：随机一个内置版式（优先同画幅）"""
    presets = load_presets()
    if not presets:
        return None
    same = [p for p in presets if p["ratio_tag"] == ratio_tag] or presets
    return random.choice(same)


# ============================================================
# 版式选择解析
# ============================================================

def parse_layout_keys(raw: str) -> List[str]:
    """把前端传的 JSON 数组解析成 ['preset:xxx', 'db:12']，去重保序"""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:
        raise LayoutError("layout_ids 不是合法的 JSON 数组")
    if not isinstance(data, list):
        raise LayoutError("layout_ids 必须是数组")
    out: List[str] = []
    for it in data:
        key = str(it or "").strip()
        if not key:
            continue
        if not (key.startswith("preset:") or key.startswith("db:")):
            raise LayoutError(f"非法版式标识: {key}")
        if key not in out:
            out.append(key)
    limit = int(getattr(get_settings(), "IMAGE_MAX_LAYOUTS_PER_TASK", 8) or 8)
    if len(out) > limit:
        raise LayoutError(f"最多选择 {limit} 个版式（当前 {len(out)} 个）")
    return out


def resolve_layouts(user_id: int, tenant_id: int, keys: List[str]) -> List[Dict[str, Any]]:
    """把标识解析成生成用的版式对象列表（保持传入顺序）"""
    if not keys:
        return []
    from database.database import SessionLocal
    from models.ai_image_layout import AIImageLayout

    db_ids = [int(k.split(":", 1)[1]) for k in keys if k.startswith("db:") and k.split(":", 1)[1].isdigit()]
    rows: Dict[int, AIImageLayout] = {}
    if db_ids:
        db = SessionLocal()
        try:
            # 官方模板（tenant 0 / source=official）对所有人可见；上传素材只看自己的
            for r in db.query(AIImageLayout).filter(
                AIImageLayout.id.in_(db_ids),
                AIImageLayout.deleted_at.is_(None),
            ).all():
                if r.source == "official" or (r.user_id == user_id and r.tenant_id == tenant_id):
                    rows[r.id] = r
        finally:
            db.close()

    out: List[Dict[str, Any]] = []
    for key in keys:
        if key.startswith("preset:"):
            p = get_preset(key.split(":", 1)[1])
            if not p:
                raise LayoutError(f"版式不存在或已下线: {key}")
            out.append(dict(p))
        else:
            rid = int(key.split(":", 1)[1])
            r = rows.get(rid)
            if not r:
                raise LayoutError(f"版式素材不存在或已删除: {key}")
            ratio_tag = r.ratio_tag or "horizontal"
            aspect = None
            if r.width and r.height:
                aspect = float(r.width) / float(r.height)
            if aspect is None:
                aspect = RATIO_TAG_ASPECT.get(ratio_tag)
            out.append({
                "key": key,
                "id": rid,
                "name": r.name or f"版式{rid}",
                "desc": "官方模板" if r.source == "official" else "上传的版式参考",
                "ratio_tag": ratio_tag,
                "aspect_ratio": aspect,
                "aspect_source": "image_size" if (r.width and r.height) else "ratio_tag",
                "width": r.width,
                "height": r.height,
                "template_version": f"db:{rid}:{r.updated_at.isoformat() if r.updated_at else ''}",
                "structure": r.structure or "",
                "zones": [],
                "image_url": r.image_url,
                "cover_url": r.thumb_url or r.image_url,
                "source": "official" if r.source == "official" else "upload",
            })
    return out


# ============================================================
# 模板执行模式与画幅兼容（P4.1）
# ============================================================

def execution_mode(layout: Dict[str, Any]) -> str:
    """模板执行模式：zones / text / reference_image / invalid"""
    has_structure = bool((layout.get("structure") or "").strip())
    has_zones = bool(layout.get("zones"))
    has_image = bool((layout.get("image_url") or "").strip())
    if has_structure and has_zones:
        return "zones"
    if has_structure:
        return "text"
    if has_image:
        return "reference_image"
    return "invalid"


def check_aplus_compatibility(layout: Dict[str, Any],
                              aplus_type: str) -> Dict[str, Any]:
    """检查模板与 A+ 画幅兼容性。

    返回 {ok, error, warnings}：
    - vertical / square 与所有 A+ 横版画布不兼容 → 拒绝；
    - 横版但比例偏差 > 20% → 允许，但返回裁剪警告（明确告知转换方式）；
    - 比例数据缺失 → 仅按 ratio_tag 判断。
    """
    warnings: List[str] = []
    mode = execution_mode(layout)
    if mode == "invalid":
        return {"ok": False, "warnings": warnings,
                "error": f"版式「{layout.get('name') or layout.get('key')}」缺少结构描述、分区数据和参考图，无法执行"}

    tag = layout.get("ratio_tag") or "horizontal"
    target = APLUS_TARGET_ASPECT.get(aplus_type)
    aspect = layout.get("aspect_ratio")
    if tag in ("vertical", "square"):
        return {"ok": False, "warnings": warnings,
                "error": (
                    f"版式「{layout.get('name') or layout.get('key')}」是{RATIO_TAG_LABELS.get(tag, tag)}"
                    f"比例，不能用于 {aplus_type} 的横版 A+ 画布；请选择横版模板或先生成适配画幅的模板"
                )}
    if target and isinstance(aspect, (int, float)) and aspect > 0:
        diff = abs(aspect - target) / target
        if diff > 0.2:
            crop = 1 - min(aspect, target) / max(aspect, target)
            side = "上下" if aspect < target else "左右"
            warnings.append(
                f"模板比例约 {aspect:.2f}:1，与目标画布 {target:.2f}:1 不一致，"
                f"生成后将按中心裁剪适配，{side}约裁掉 {crop * 100:.0f}%；"
                f"请确认关键内容不贴近{side}边缘"
            )
    if mode == "reference_image":
        warnings.append(
            "该模板只有参考图、没有结构描述：生成时只借用参考图的分区与留白，"
            "其中的商品、文字、配色与 logo 不会带入；建议补充结构描述以获得稳定版式"
        )
    return {"ok": True, "warnings": warnings, "error": None}


# ============================================================
# 提示词片段
# ============================================================

def layout_clause(layout: Dict[str, Any], no_text: bool, ref_index: Optional[int] = None) -> str:
    """版式 → 写进提示词的分区约束

    上传版式没有 structure，只能靠「第 N 张参考图」指位；此时必须声明
    「只借分区，不搬里面的商品/文字」，否则模型会把版式图里的商品也画进去。
    """
    text_rule = (
        # 2026-10-29 用户裁定：不要「留白块/留白条」——它们暗示一块可见的矩形。
        # 留白必须是背景的自然延伸，不是贴在背景上的一块东西。
        "文案区做成与背景连续的整片留白，不留矩形色块、边框、卡片或阴影分界，"
        "画面里也不得渲染任何文字、字母、数字、价签或 logo。"
        if no_text else
        "文案区可排布简短文案，语言与目标市场一致，字号层级分明，不得出现价签、水印或 logo。"
    )
    if layout.get("structure"):
        return (
            "\n\n版式（画面分区必须按此执行，商品本身仍以参考图为准）：\n"
            f"{layout['structure']}\n{text_rule}"
        )
    if not ref_index:
        # 没有分区文字、版式图也没送到（CDN 取回或 ComfyUI 上传失败）：
        # 此时写「照第 N 张参考图」会指向一张不存在的图，模型只会按自己的偏好乱排，
        # 属于「看似有约束实际无约束」的静默降级 —— 直接不注入，让调用方日志里能看出没生效。
        return ""
    idx = ref_index
    return (
        "\n\n版式（画面分区必须按此执行）：严格照第 "
        f"{idx} 张参考图的分区位置、留白比例与视觉层级组织结构；"
        "只借用它的分区与留白，其中出现的商品、文案、配色与 logo 一律不得带入画面。"
        f"\n{text_rule}"
    )


# ============================================================
# 素材库（上传 / 收藏 / 列表）
# ============================================================

def list_layouts(user_id: int, tenant_id: int) -> Dict[str, Any]:
    """返回三组数据：官方推荐（官方模板 + 内置结构版式）/ 我的（上传）/ 收藏"""
    from database.database import SessionLocal
    from models.ai_image_layout import AIImageLayout

    presets = load_presets()
    db = SessionLocal()
    try:
        rows = db.query(AIImageLayout).filter(
            AIImageLayout.deleted_at.is_(None),
        ).filter(
            # 官方模板全租户可见；上传素材与收藏只看自己的
            (AIImageLayout.source == "official") |
            ((AIImageLayout.user_id == user_id) & (AIImageLayout.tenant_id == tenant_id))
        ).order_by(AIImageLayout.id.asc()).all()

        # 收藏集合：内置版式存 preset_id=版式id；官方模板存 preset_id='db:<id>'
        fav_keys = {r.preset_id for r in rows if r.source == "favorite" and r.preset_id}

        official = [{
            "key": f"db:{r.id}", "id": r.id,
            "name": r.name or f"官方模板{r.id}",
            "desc": r.structure or "官方模板",
            "ratio_tag": r.ratio_tag or "horizontal",
            "structure": r.structure or "",
            "zones": [],
            "image_url": r.image_url,
            "cover_url": r.thumb_url or r.image_url,
            "source": "official",
            "favorited": f"db:{r.id}" in fav_keys,
        } for r in rows if r.source == "official"]

        mine = [{
            "key": f"db:{r.id}", "id": r.id, "name": r.name or f"我的版式{r.id}",
            "ratio_tag": r.ratio_tag or "horizontal", "image_url": r.image_url,
            "cover_url": r.thumb_url or r.image_url,
            "favorite": bool(r.favorite), "width": r.width, "height": r.height,
            "structure": r.structure or "",
            "source": "upload",
        } for r in rows if r.source == "upload"]

        # 结构版式殿后：它们靠文字分区描述驱动，是「没有参考图时」的稳定基线
        structural = []
        for p in presets:
            item = dict(p)
            item["favorited"] = p["id"] in fav_keys
            structural.append(item)

        favorites = [p for p in structural if p["favorited"]]
        favorites += [o for o in official if o["favorited"]]
        favorites += [m for m in mine if m.get("favorite")]
    finally:
        db.close()

    return {
        "presets": official + structural,
        "mine": mine,
        "favorites": favorites,
        "counts": {"official": len(official), "structural": len(structural), "mine": len(mine)},
        "limit": int(getattr(get_settings(), "IMAGE_MAX_LAYOUTS_PER_TASK", 8) or 8),
        "ratio_tags": [{"value": t, "label": RATIO_TAG_LABELS[t]} for t in RATIO_TAGS],
    }


def add_upload(user_id: int, tenant_id: int, user_name: str,
               content: bytes, file_name: str, name: str = "") -> Dict[str, Any]:
    """保存用户上传的版式参考图（TOS + 落库）"""
    from PIL import Image as PILImage
    import io as _io
    from database.database import SessionLocal
    from models.ai_image_layout import AIImageLayout
    from services import tos_service

    try:
        with PILImage.open(_io.BytesIO(content)) as im:
            width, height = im.size
    except Exception:
        raise LayoutError("版式图无法解析")

    ratio_tag = "horizontal"
    if height > width * 1.15:
        ratio_tag = "vertical"
    elif abs(width - height) <= min(width, height) * 0.15:
        ratio_tag = "square"

    try:
        url = tos_service.upload_file(content, file_name or "layout.png",
                                      subdir="ai-image-layouts")
    except Exception as e:
        logger.error(f"[版式] 上传 TOS 失败: {e}")
        raise LayoutError(f"版式图上传失败: {e}")

    db = SessionLocal()
    try:
        row = AIImageLayout(
            tenant_id=tenant_id, user_id=user_id, source="upload",
            name=(name or os.path.splitext(file_name or "")[0] or "我的版式")[:100],
            image_url=url, ratio_tag=ratio_tag, width=width, height=height,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return {
            "key": f"db:{row.id}", "id": row.id, "name": row.name,
            "ratio_tag": row.ratio_tag, "image_url": row.image_url,
            "favorite": False, "width": row.width, "height": row.height,
        }
    finally:
        db.close()


def set_favorite(user_id: int, tenant_id: int, key: str, favorite: bool) -> Dict[str, Any]:
    """收藏/取消收藏：内置版式写 favorite 行，上传版式改 favorite 字段"""
    from database.database import SessionLocal
    from models.ai_image_layout import AIImageLayout

    if not (key.startswith("preset:") or key.startswith("db:")):
        raise LayoutError(f"非法版式标识: {key}")

    db = SessionLocal()
    try:
        if key.startswith("preset:"):
            pid = key.split(":", 1)[1]
            preset = get_preset(pid)
            if not preset:
                raise LayoutError(f"版式不存在: {pid}")
            row = db.query(AIImageLayout).filter(
                AIImageLayout.user_id == user_id,
                AIImageLayout.tenant_id == tenant_id,
                AIImageLayout.source == "favorite",
                AIImageLayout.preset_id == pid,
            ).first()
            if favorite:
                if row is None:
                    db.add(AIImageLayout(tenant_id=tenant_id, user_id=user_id,
                                         source="favorite", preset_id=pid,
                                         name=preset["name"], ratio_tag=preset["ratio_tag"],
                                         favorite=True))
                elif row.deleted_at is not None:
                    row.deleted_at = None
            elif row is not None:
                db.delete(row)
            db.commit()
            return {"key": key, "favorited": bool(favorite)}

        rid = int(key.split(":", 1)[1])
        row = db.query(AIImageLayout).filter(
            AIImageLayout.id == rid,
            AIImageLayout.deleted_at.is_(None),
        ).first()
        if not row:
            raise LayoutError("版式素材不存在")
        if row.source == "official":
            # 官方模板是共享行：收藏不能写 favorite 字段（会影响所有人），
            # 改为插入一条 preset_id='db:<id>' 的收藏记录
            mark = f"db:{rid}"
            fav_row = db.query(AIImageLayout).filter(
                AIImageLayout.user_id == user_id,
                AIImageLayout.tenant_id == tenant_id,
                AIImageLayout.source == "favorite",
                AIImageLayout.preset_id == mark,
            ).first()
            if favorite and fav_row is None:
                db.add(AIImageLayout(tenant_id=tenant_id, user_id=user_id,
                                     source="favorite", preset_id=mark,
                                     name=row.name, ratio_tag=row.ratio_tag, favorite=True))
            elif not favorite and fav_row is not None:
                db.delete(fav_row)
            db.commit()
            return {"key": key, "favorited": bool(favorite)}
        if row.user_id != user_id or row.tenant_id != tenant_id:
            raise LayoutError("版式素材不存在")
        row.favorite = bool(favorite)
        db.commit()
        return {"key": key, "favorited": bool(favorite)}
    finally:
        db.close()


def delete_layout(user_id: int, tenant_id: int, layout_id: int) -> None:
    """软删用户上传的版式"""
    from datetime import datetime
    from database.database import SessionLocal
    from models.ai_image_layout import AIImageLayout

    db = SessionLocal()
    try:
        row = db.query(AIImageLayout).filter(
            AIImageLayout.id == layout_id,
            AIImageLayout.user_id == user_id,
            AIImageLayout.tenant_id == tenant_id,
            AIImageLayout.source == "upload",
            AIImageLayout.deleted_at.is_(None),
        ).first()
        if not row:
            raise LayoutError("版式素材不存在或无权删除")
        row.deleted_at = datetime.now()
        db.commit()
    finally:
        db.close()


# ============================================================
# 版式结构描述（structure）：人工编辑 + AI 草稿
# ============================================================

# 读图写分区描述。刻意不问「这是什么商品」——只问版面，避免模型把别人的商品写进来。
STRUCTURE_PROMPT = """你是电商 A+ 版式分析器。看这张版式参考图，输出一段**中文**分区结构描述，不超过 160 字。

要求：
1. 只描述画面分区与视觉层级：分成几个区、各自大致位置与占比、哪个区是商品图、哪个区是文字区或色块条。
2. 若有贯通整宽的色条、硬边分割、多格拼接，明确说出几段、怎么分。
3. 不要识别或描述商品本身，不要评价美观程度，不要提品牌与文字内容。
4. 用陈述句，形如「左侧约40%为文案区，自上而下主标题与两行副文案；右侧约60%为商品大图」。
5. 只输出描述本身：不要前后缀、不要 Markdown、不要换行分点。"""


def _fetch_layout_image(url: str, limit: int = 10 * 1024 * 1024) -> bytes:
    """取版式图字节（直连，不走系统代理）"""
    if not url:
        raise LayoutError("该版式没有参考图")
    try:
        import requests

        sess = requests.Session()
        sess.trust_env = False
        resp = sess.get(url, timeout=20, stream=True)
        resp.raise_for_status()
        buf = bytearray()
        for chunk in resp.iter_content(65536):
            buf.extend(chunk)
            if len(buf) > limit:
                raise LayoutError("版式图超过 10MB")
        return bytes(buf)
    except LayoutError:
        raise
    except Exception as e:
        raise LayoutError(f"版式图下载失败: {e}")


def _load_row_for_edit(user_id: int, tenant_id: int, layout_id: int):
    """编辑 structure 时取行：官方模板（共享）与本人上传都允许"""
    from database.database import SessionLocal
    from models.ai_image_layout import AIImageLayout

    db = SessionLocal()
    try:
        row = db.query(AIImageLayout).filter(
            AIImageLayout.id == layout_id,
            AIImageLayout.deleted_at.is_(None),
        ).first()
        if not row:
            raise LayoutError("版式不存在")
        if row.source != "official" and not (
            row.user_id == user_id and row.tenant_id == tenant_id
        ):
            raise LayoutError("版式不存在或无权编辑")
        return row
    finally:
        db.close()


def update_structure(user_id: int, tenant_id: int, layout_id: int, structure: str) -> Dict[str, Any]:
    """保存版式结构描述（写入后下一次生成即带文字约束）"""
    from database.database import SessionLocal
    from models.ai_image_layout import AIImageLayout

    text = (structure or "").strip()
    if len(text) > 500:
        raise LayoutError("结构描述过长（限 500 字以内）")
    _load_row_for_edit(user_id, tenant_id, layout_id)

    db = SessionLocal()
    try:
        row = db.query(AIImageLayout).filter(AIImageLayout.id == layout_id).first()
        row.structure = text or None
        db.commit()
        return {"id": layout_id, "structure": row.structure or ""}
    finally:
        db.close()


def suggest_structure(user_id: int, tenant_id: int, layout_id: int) -> Dict[str, Any]:
    """AI 读图产出结构描述草稿（不落库，由前端确认后再保存）"""
    row = _load_row_for_edit(user_id, tenant_id, layout_id)
    data = _fetch_layout_image(row.image_url or "")

    from services import relay_image_service as relay

    chan = relay._analyze_channel()
    if not (chan.get("base") and chan.get("key") and chan.get("model")):
        raise LayoutError("视觉模型未配置（缺 OPENAI_API_BASE/KEY 或 IMAGE_RELAY_*）")
    small = relay._shrink_for_analysis(data)
    content = [
        {"type": "text", "text": STRUCTURE_PROMPT},
        {"type": "image_url", "image_url": {"url": relay._image_data_uri(small)}},
    ]
    try:
        text = relay._post_chat(
            chan["base"], chan["key"], chan["model"], content, 90,
            label="视觉模型", max_tokens=500,
        )
    except Exception as e:
        raise LayoutError(f"AI 生成结构描述失败: {e}")
    text = (text or "").strip().strip("。") + "。"
    if len(text) < 10:
        raise LayoutError("AI 返回的结构描述为空，请手动填写")
    return {"id": layout_id, "structure": text[:500], "name": row.name}
