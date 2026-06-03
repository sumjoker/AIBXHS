"""
店铺名映射工具
用于处理数据库店铺名与库存数据店铺名的映射关系
"""
from typing import Optional, Dict, List, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func
from models.store import Store


# 店铺映射规则
# 格式: (数据库店铺名模式, 站点) -> 库存店铺名
STORE_MAPPING_RULES = {
    # 云南金顺公司：JeVenis-国家
    ("云南金顺公司", "US"): "JeVenis-US",
    ("云南金顺公司", "CA"): "JeVenis-CA",
    ("云南金顺公司", "MX"): "JeVenis-MX",
    ("云南金顺公司", "BR"): "JeVenis-BR",
    ("云南金顺公司", "DE"): "JeVenis-DE",
    ("云南金顺公司", "FR"): "JeVenis-FR",
    ("云南金顺公司", "IT"): "JeVenis-IT",
    ("云南金顺公司", "ES"): "JeVenis-ES",
    ("云南金顺公司", "UK"): "JeVenis-UK",
    ("云南金顺公司", "JP"): "JeVenis-JP",
    ("云南金顺公司", "AU"): "JeVenis-AU",

    # B账号账户管理：LaVenty-US、LaVenty-US-BR(美国、巴西)
    ("B账号", "US"): "LaVenty-US",
    ("B账号", "BR"): "LaVenty-US-BR",

    # C账号账户管理：Roaring-US、Roaring-BR(美国、巴西)
    ("C账号", "US"): "Roaring-US",
    ("C账号", "BR"): "Roaring-BR",

    # D账号账户管理: D-USA-US、D-USA-CA(美国、加拿大)
    ("D账号", "US"): "D-USA-US",
    ("D账号", "CA"): "D-USA-CA",

    # E账号账户管理: E-DE(德国)
    ("E账号", "DE"): "E-DE",

    # F账号账户管理：F-DE、F-FR、F-IT、F-ES、F-NL、F-SE、F-PL、F-BE
    ("F账号", "DE"): "F-DE",
    ("F账号", "FR"): "F-FR",
    ("F账号", "IT"): "F-IT",
    ("F账号", "ES"): "F-ES",
    ("F账号", "NL"): "F-NL",
    ("F账号", "SE"): "F-SE",
    ("F账号", "PL"): "F-PL",
    ("F账号", "BE"): "F-BE",

    # G站点紫鸟账号: G-USA、G-CA、G-MX(美国、加拿大、墨西哥)
    ("G站点", "US"): "G-USA",
    ("G站点", "CA"): "G-CA",
    ("G站点", "MX"): "G-MX",
}


def get_inventory_account(store_name: str, site: str) -> str:
    """
    根据数据库店铺名和站点获取库存数据中的店铺账号

    Args:
        store_name: 数据库中的店铺名
        site: 站点代码(US/CA/DE等)

    Returns:
        库存数据中的店铺账号格式
    """
    # 直接匹配完整店铺名
    for (pattern, site_code), inventory_name in STORE_MAPPING_RULES.items():
        if pattern == store_name and site_code == site:
            return f"{inventory_name}-{site}"

    # 模糊匹配（包含关键词）
    for (pattern, site_code), inventory_name in STORE_MAPPING_RULES.items():
        if pattern in store_name and site_code == site:
            return f"{inventory_name}-{site}"

    # 默认返回原店铺名-站点
    return f"{store_name}-{site}"


def parse_inventory_account(account: str) -> Tuple[str, str]:
    """
    解析库存数据中的店铺账号

    Args:
        account: 库存数据中的店铺账号，如 "JeVenis-US"

    Returns:
        (店铺名, 站点)
    """
    if not account:
        return ("", "")

    # 处理特殊格式：LaVenty-US-BR
    parts = account.split("-")
    if len(parts) >= 2:
        # 最后一部分是站点
        site = parts[-1]
        # 前面部分是店铺名
        store_name = "-".join(parts[:-1])
        return (store_name, site)

    return (account, "")


def get_store_mapping_from_db(db: Session, tenant_id: int = 1) -> Dict[str, Store]:
    """
    从数据库获取店铺映射

    Args:
        db: 数据库会话
        tenant_id: 租户ID

    Returns:
        映射字典: {inventory_account: Store}
    """
    stores = db.query(Store).filter(
        Store.tenant_id == tenant_id,
        Store.inventory_name.isnot(None)
    ).all()

    mapping = {}
    for store in stores:
        # 构建库存账号格式
        inventory_account = f"{store.inventory_name}-{store.site}"
        mapping[inventory_account] = store

    return mapping


def get_store_by_inventory_account(
    db: Session,
    inventory_account: str,
    tenant_id: int = 1
) -> Optional[Store]:
    """
    根据库存账号查找对应的店铺

    Args:
        db: 数据库会话
        inventory_account: 库存数据中的店铺账号，如 "JeVenis-US"
        tenant_id: 租户ID

    Returns:
        Store对象或None
    """
    # 解析库存账号
    store_name, site = parse_inventory_account(inventory_account)

    # 先尝试精确匹配 inventory_name
    store = db.query(Store).filter(
        Store.tenant_id == tenant_id,
        Store.inventory_name == store_name,
        Store.site == site
    ).first()

    if store:
        return store

    # 如果没有设置 inventory_name，使用映射规则
    for (pattern, site_code), inv_name in STORE_MAPPING_RULES.items():
        if inv_name == store_name and site_code == site:
            # 模糊匹配店铺名
            store = db.query(Store).filter(
                Store.tenant_id == tenant_id,
                Store.name.like(f"%{pattern}%"),
                Store.site == site
            ).first()
            if store:
                return store

    return None


def get_all_store_mappings(db: Session, tenant_id: int = 1) -> List[Dict]:
    """
    获取所有店铺映射关系

    Args:
        db: 数据库会话
        tenant_id: 租户ID

    Returns:
        映射列表
    """
    stores = db.query(Store).filter(
        Store.tenant_id == tenant_id
    ).all()

    result = []
    for store in stores:
        # 如果有 inventory_name，使用它
        if store.inventory_name:
            inventory_account = f"{store.inventory_name}-{store.site}"
        else:
            # 否则使用映射规则
            inventory_account = get_inventory_account(store.name, store.site or "")

        result.append({
            "store_id": store.id,
            "store_name": store.name,
            "site": store.site,
            "inventory_name": store.inventory_name,
            "inventory_account": inventory_account,
            "platform": store.platform.value if store.platform else None,
        })

    return result


def auto_update_store_inventory_names(db: Session, tenant_id: int = 1) -> Dict:
    """
    自动更新店铺的 inventory_name 字段

    Args:
        db: 数据库会话
        tenant_id: 租户ID

    Returns:
        更新统计
    """
    stores = db.query(Store).filter(
        Store.tenant_id == tenant_id
    ).all()

    updated_count = 0
    skipped_count = 0

    for store in stores:
        # 查找匹配的映射规则
        inventory_name = None
        for (pattern, site_code), inv_name in STORE_MAPPING_RULES.items():
            if pattern in store.name and site_code == store.site:
                inventory_name = inv_name
                break

        if inventory_name and store.inventory_name != inventory_name:
            store.inventory_name = inventory_name
            updated_count += 1
        else:
            skipped_count += 1

    db.commit()

    return {
        "total": len(stores),
        "updated": updated_count,
        "skipped": skipped_count
    }
