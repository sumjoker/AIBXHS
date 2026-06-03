#!/usr/bin/env python3
"""
根据实际数据库数据更新店铺映射
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import pymysql
import json
from datetime import datetime

# 数据库配置
DB_CONFIG = {
    'host': '115.190.250.14',
    'port': 3306,
    'user': 'bxhs_ai_assistance',
    'password': 'bxhsaiRoot@123',
    'database': 'bxhs_ai_assistance',
    'charset': 'utf8mb4'
}

# 站点中文名到代码的映射
SITE_MAPPING = {
    "美国": "US",
    "英国": "UK",
    "德国": "DE",
    "法国": "FR",
    "意大利": "IT",
    "西班牙": "ES",
    "荷兰": "NL",
    "瑞典": "SE",
    "波兰": "PL",
    "比利时": "BE",
    "加拿大": "CA",
    "墨西哥": "MX",
    "巴西": "BR",
    "日本": "JP",
    "澳大利亚": "AU",
}

# 店铺名映射规则（基于实际数据库数据）
# 格式: (数据库店铺名, 中文站点) -> inventory_name
STORE_MAPPING = {
    # 云南金顺公司
    ("云南金顺公司", "美国"): "JeVenis-US",
    ("云南金顺公司", "加拿大"): "JeVenis-CA",
    ("云南金顺公司", "墨西哥"): "JeVenis-MX",
    ("云南金顺公司", "巴西"): "JeVenis-BR",
    ("云南金顺公司", "德国"): "JeVenis-DE",
    ("云南金顺公司", "法国"): "JeVenis-FR",
    ("云南金顺公司", "意大利"): "JeVenis-IT",
    ("云南金顺公司", "西班牙"): "JeVenis-ES",
    ("云南金顺公司", "英国"): "JeVenis-UK",
    ("云南金顺公司", "日本"): "JeVenis-JP",
    ("云南金顺公司", "澳大利亚"): "JeVenis-AU",
    ("云南金顺公司", "荷兰"): "JeVenis-NL",

    # B账号
    ("B账号账户管理", "美国"): "LaVenty-US",
    ("B账号账户管理", "巴西"): "LaVenty-US-BR",
    ("B账号账户管理", "加拿大"): "LaVenty-CA",
    ("B账号账户管理", "墨西哥"): "LaVenty-MX",
    ("B账号账户管理", "荷兰"): "LaVenty-NL",

    # C账号
    ("C账号账户管理", "美国"): "Roaring-US",
    ("C账号账户管理", "巴西"): "Roaring-BR",
    ("C账号账户管理", "加拿大"): "Roaring-CA",
    ("C账号账户管理", "墨西哥"): "Roaring-MX",
    ("C账号账户管理", "荷兰"): "Roaring-NL",

    # D账号
    ("D账号账户管理", "美国"): "D-USA-US",
    ("D账号账户管理", "加拿大"): "D-USA-CA",
    ("D账号账户管理", "墨西哥"): "D-USA-MX",
    ("D账号账户管理", "荷兰"): "D-USA-NL",
    ("D账号账户管理", "法国"): "D-USA-FR",

    # E账号
    ("E账号账户管理", "德国"): "E-DE",
    ("E账号账户管理", "意大利"): "E-IT",
    ("E账号账户管理", "荷兰"): "E-NL",
    ("E账号账户管理", "加拿大"): "E-CA",
    ("E账号账户管理", "墨西哥"): "E-MX",

    # F账号
    ("F账号账户管理", "德国"): "F-DE",
    ("F账号账户管理", "法国"): "F-FR",
    ("F账号账户管理", "意大利"): "F-IT",
    ("F账号账户管理", "西班牙"): "F-ES",
    ("F账号账户管理", "荷兰"): "F-NL",
    ("F账号账户管理", "加拿大"): "F-CA",
    ("F账号账户管理", "墨西哥"): "F-MX",

    # G站点
    ("G站点紫鸟账号", "美国"): "G-USA",
    ("G站点紫鸟账号", "加拿大"): "G-CA",
    ("G站点紫鸟账号", "墨西哥"): "G-MX",
    ("G站点紫鸟账号", "荷兰"): "G-NL",

    # 直接匹配库存店铺名的情况
    ("JeVenis", "美国"): "JeVenis-US",
    ("LaVenty", "英国"): "LaVenty-UK",
    ("roaring", "德国"): "Roaring-DE",
}


def get_inventory_name(store_name: str, site: str) -> str:
    """根据店铺名和站点获取库存店铺名"""
    # 直接匹配
    key = (store_name, site)
    if key in STORE_MAPPING:
        return STORE_MAPPING[key]

    # 模糊匹配
    for (pattern, site_code), inv_name in STORE_MAPPING.items():
        if pattern in store_name and site_code == site:
            return inv_name

    return None


def main():
    print("=" * 60)
    print("更新店铺映射数据")
    print("=" * 60)

    try:
        # 连接数据库
        print("\n🔗 连接数据库...")
        conn = pymysql.connect(**DB_CONFIG)
        cursor = conn.cursor()
        print("✅ 数据库连接成功")

        # 查询所有店铺
        cursor.execute("SELECT id, name, site, inventory_name FROM stores WHERE tenant_id = 1")
        stores = cursor.fetchall()

        print(f"\n📊 共找到 {len(stores)} 个店铺")
        print("\n🔄 开始更新映射...")

        updated_count = 0
        skipped_count = 0
        update_details = []

        for store in stores:
            store_id, name, site, current_inv_name = store

            # 如果已经有 inventory_name，跳过
            if current_inv_name:
                skipped_count += 1
                continue

            # 计算映射值
            inventory_name = get_inventory_name(name, site)

            if inventory_name:
                cursor.execute(
                    "UPDATE stores SET inventory_name = %s WHERE id = %s",
                    (inventory_name, store_id)
                )
                updated_count += 1
                update_details.append({
                    "id": store_id,
                    "name": name,
                    "site": site,
                    "inventory_name": inventory_name
                })
                print(f"   ✓ {name} ({site}) -> {inventory_name}")
            else:
                skipped_count += 1
                print(f"   ⚠ {name} ({site}) 未找到匹配规则")

        conn.commit()
        conn.close()

        print(f"\n📊 更新统计:")
        print(f"   成功: {updated_count}")
        print(f"   跳过: {skipped_count}")

        # 保存更新详情
        report_file = f"mapping_update_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        report_path = os.path.join(os.path.dirname(__file__), report_file)

        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(update_details, f, ensure_ascii=False, indent=2)

        print(f"\n✅ 更新完成！报告已保存: {report_path}")

    except Exception as e:
        print(f"\n❌ 更新失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
