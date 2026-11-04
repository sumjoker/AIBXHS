# -*- coding: utf-8 -*-
"""A+ 商品事实卡：结构化 Schema、校验与提示词渲染（P4）。

设计原则：
- 事实卡是商品事实的唯一来源；未知项写「待确认」，不允许模型猜测。
- components 描述整套销售商品；coverage_plan 描述整张画布的组件覆盖与分区分配。
- 不再要求每个商品图区重复完整套装；展示性重复不等于实际数量。
- excluded_objects 中的物体禁止出现在成图，也不得作为场景道具重新引入。
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

UNKNOWN = "待确认"
SCENE_TYPES = ("usage", "context", "display")


class FactCardError(Exception):
    """事实卡校验失败（对上层暴露可读原因列表）"""


# JSON Schema（供前端/文档使用；后端校验用 validate_fact_card 手写实现，避免新增依赖）
FACT_CARD_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": [
        "product_identity", "components", "excluded_objects",
        "visual_identity", "global_requirements", "coverage_plan",
    ],
    "properties": {
        "product_identity": {
            "type": "object",
            "required": ["name", "category", "usage", "target_audience", "theme"],
            "properties": {
                "name": {"type": "string"},
                "category": {"type": "string"},
                "usage": {"type": "string"},
                "target_audience": {"type": "string"},
                "theme": {"type": "string"},
            },
        },
        "components": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": [
                    "id", "name", "quantity", "color", "material",
                    "printed_details", "is_sold_item", "must_preserve",
                ],
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "quantity": {"anyOf": [
                        {"type": "integer", "minimum": 0},
                        {"type": "string", "enum": [UNKNOWN]},
                    ]},
                    "color": {"type": "array", "items": {"type": "string"}},
                    "material": {"type": "string"},
                    "printed_details": {"type": "array", "items": {"type": "string"}},
                    "is_sold_item": {"type": "boolean"},
                    "must_preserve": {"type": "boolean"},
                },
            },
        },
        "excluded_objects": {"type": "array", "items": {"type": "string"}},
        "visual_identity": {
            "type": "object",
            "required": ["theme", "color_direction", "photography_style"],
            "properties": {
                "theme": {"type": "string"},
                "color_direction": {"type": "string"},
                "photography_style": {"type": "string"},
            },
        },
        "global_requirements": {"type": "array", "items": {"type": "string"}},
        "coverage_plan": {
            "type": "object",
            "required": ["strategy", "global_quantity_rule", "zones"],
            "properties": {
                "strategy": {"type": "string"},
                "global_quantity_rule": {"type": "string"},
                "zones": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["zone_id", "scene_type", "component_ids"],
                        "properties": {
                            "zone_id": {"type": "string"},
                            "scene_type": {"type": "string", "enum": list(SCENE_TYPES)},
                            "component_ids": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
            },
        },
    },
}


def normalize_fact_card(card: Dict[str, Any]) -> Dict[str, Any]:
    """深拷贝并补默认值；不修正内容，校验交给 validate_fact_card。"""
    out = copy.deepcopy(card or {})
    out.setdefault("components", [])
    out.setdefault("excluded_objects", [])
    out.setdefault("global_requirements", [])
    out.setdefault("coverage_plan", {})
    for comp in out.get("components") or []:
        if not isinstance(comp, dict):
            continue
        comp.setdefault("color", [])
        comp.setdefault("material", "")
        comp.setdefault("printed_details", [])
        comp.setdefault("is_sold_item", True)
        comp.setdefault("must_preserve", True)
    return out


def _req_str(obj: Dict[str, Any], key: str, label: str, errors: List[str]) -> None:
    val = obj.get(key) if isinstance(obj, dict) else None
    if not isinstance(val, str) or not val.strip():
        errors.append(f"{label}.{key} 缺失或为空")


def validate_fact_card(card: Optional[Dict[str, Any]]) -> List[str]:
    """校验事实卡；返回错误列表（空表示通过）。"""
    errors: List[str] = []
    if not isinstance(card, dict):
        return ["事实卡必须是 JSON 对象"]

    for key in ("product_identity", "components", "excluded_objects",
                "visual_identity", "global_requirements", "coverage_plan"):
        if key not in card:
            errors.append(f"缺少字段: {key}")
    if errors:
        return errors

    ident = card.get("product_identity") or {}
    if not isinstance(ident, dict):
        errors.append("product_identity 必须是对象")
    else:
        for key in ("name", "category", "usage", "target_audience", "theme"):
            _req_str(ident, key, "product_identity", errors)

    comps = card.get("components")
    if not isinstance(comps, list) or not comps:
        errors.append("components 必须是非空数组")
        comps = []
    ids = set()
    sold_ids = set()
    for i, comp in enumerate(comps):
        if not isinstance(comp, dict):
            errors.append(f"components[{i}] 必须是对象")
            continue
        cid = comp.get("id")
        if not isinstance(cid, str) or not cid.strip():
            errors.append(f"components[{i}].id 缺失或为空")
        elif cid in ids:
            errors.append(f"components[{i}].id 重复: {cid}")
        else:
            ids.add(cid)
        _req_str(comp, "name", f"components[{i}]", errors)
        qty = comp.get("quantity")
        if not (isinstance(qty, int) and qty >= 0) and qty != UNKNOWN:
            errors.append(f"components[{i}].quantity 必须是 >=0 的整数或「{UNKNOWN}」")
        for key in ("color", "printed_details"):
            if not isinstance(comp.get(key), list):
                errors.append(f"components[{i}].{key} 必须是数组")
        for key in ("is_sold_item", "must_preserve"):
            if not isinstance(comp.get(key), bool):
                errors.append(f"components[{i}].{key} 必须是布尔值")
        if comp.get("is_sold_item") is True and isinstance(cid, str):
            sold_ids.add(cid)
    if not sold_ids:
        errors.append("components 至少需要一个 is_sold_item=true 的销售商品")

    excluded = card.get("excluded_objects")
    if not isinstance(excluded, list) or any(not isinstance(x, str) for x in excluded):
        errors.append("excluded_objects 必须是字符串数组")

    visual = card.get("visual_identity") or {}
    if not isinstance(visual, dict):
        errors.append("visual_identity 必须是对象")
    else:
        for key in ("theme", "color_direction", "photography_style"):
            _req_str(visual, key, "visual_identity", errors)

    reqs = card.get("global_requirements")
    if not isinstance(reqs, list) or any(not isinstance(x, str) for x in reqs):
        errors.append("global_requirements 必须是字符串数组")

    plan = card.get("coverage_plan") or {}
    if not isinstance(plan, dict):
        errors.append("coverage_plan 必须是对象")
    else:
        _req_str(plan, "strategy", "coverage_plan", errors)
        _req_str(plan, "global_quantity_rule", "coverage_plan", errors)
        zones = plan.get("zones")
        if not isinstance(zones, list) or not zones:
            errors.append("coverage_plan.zones 必须是非空数组")
            zones = []
        covered = set()
        zone_ids = set()
        for i, zone in enumerate(zones):
            if not isinstance(zone, dict):
                errors.append(f"coverage_plan.zones[{i}] 必须是对象")
                continue
            zid = zone.get("zone_id")
            if not isinstance(zid, str) or not zid.strip():
                errors.append(f"coverage_plan.zones[{i}].zone_id 缺失或为空")
            elif zid in zone_ids:
                errors.append(f"coverage_plan.zones[{i}].zone_id 重复: {zid}")
            else:
                zone_ids.add(zid)
            if zone.get("scene_type") not in SCENE_TYPES:
                errors.append(
                    f"coverage_plan.zones[{i}].scene_type 必须是 {SCENE_TYPES}"
                )
            cids = zone.get("component_ids")
            if not isinstance(cids, list) or not cids:
                errors.append(f"coverage_plan.zones[{i}].component_ids 必须是非空数组")
                continue
            for cid in cids:
                if cid not in ids:
                    errors.append(f"coverage_plan.zones[{i}] 引用了不存在的组件: {cid}")
                elif cid in sold_ids:
                    covered.add(cid)
        missing = sorted(sold_ids - covered)
        if missing:
            errors.append(f"coverage_plan 未覆盖以下销售组件: {missing}")

    return errors


def render_fact_card(card: Dict[str, Any]) -> str:
    """事实卡 → 提示词文本（541 类纯函数，无 DB/网络依赖）。"""
    ident = card.get("product_identity") or {}
    lines: List[str] = ["【结构化商品事实卡 —— 唯一事实来源，不得改写字段】"]
    lines.append(
        "商品：{name}｜类目：{category}｜主题：{theme}｜用途：{usage}｜目标人群：{audience}".format(
            name=ident.get("name", ""), category=ident.get("category", ""),
            theme=ident.get("theme", ""), usage=ident.get("usage", ""),
            audience=ident.get("target_audience", ""),
        )
    )

    comps = card.get("components") or []
    sold = [c for c in comps if isinstance(c, dict) and c.get("is_sold_item")]
    lines.append("组件清单（销售商品，实际数量以本清单为准）：")
    for i, comp in enumerate(sold, start=1):
        qty = comp.get("quantity", UNKNOWN)
        colors = "、".join(comp.get("color") or []) or "—"
        prints = "、".join(comp.get("printed_details") or []) or "无"
        lines.append(
            f"{i}. {comp.get('name', '')} ×{qty}；颜色：{colors}；"
            f"材质：{comp.get('material') or '—'}；印刷：{prints}；"
            f"必须保留：{'是' if comp.get('must_preserve') else '否'}"
        )

    excluded = card.get("excluded_objects") or []
    if excluded:
        lines.append("排除对象（禁止出现在成图，也不得作为场景道具重新引入）："
                     + "、".join(excluded))

    visual = card.get("visual_identity") or {}
    lines.append(
        "视觉方向：主题 {t}；色彩 {c}；摄影 {p}".format(
            t=visual.get("theme", ""), c=visual.get("color_direction", ""),
            p=visual.get("photography_style", ""),
        )
    )

    reqs = card.get("global_requirements") or []
    if reqs:
        lines.append("全局要求：" + "；".join(reqs))

    plan = card.get("coverage_plan") or {}
    lines.append(
        "覆盖计划：{s}；数量规则：{r}".format(
            s=plan.get("strategy", ""), r=plan.get("global_quantity_rule", ""),
        )
    )
    by_id = {c.get("id"): c for c in comps if isinstance(c, dict)}
    for zone in plan.get("zones") or []:
        names = [
            (by_id.get(cid) or {}).get("name", cid)
            for cid in (zone.get("component_ids") or [])
        ]
        lines.append(
            f"- 分区 {zone.get('zone_id')}（{zone.get('scene_type')}）："
            + "、".join(names)
        )

    uncertain = [
        f"{c.get('name')}（{c.get('id')}）"
        for c in sold if c.get("quantity") == UNKNOWN
    ]
    if uncertain:
        lines.append("待确认项（保持待确认，不得猜测）：" + "、".join(uncertain))
    return "\n".join(lines)
