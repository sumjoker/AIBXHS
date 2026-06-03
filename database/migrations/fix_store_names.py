#!/usr/bin/env python3
"""
修复店铺名称
将 name 字段恢复为正确的店铺名（云南金顺公司、B账号账户管理等）
同时保留 inventory_name 用于库存映射
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

# 正确的店铺名映射
# 根据 inventory_name 推断正确的 name
STORE_NAME_FIX = {
    # JeVenis 系列 -> 云南金顺公司
    "JeVenis-US": "云南金顺公司",
    "JeVenis-CA": "云南金顺公司",
    "JeVenis-MX": "云南金顺公司",
    "JeVenis-BR": "云南金顺公司",
    "JeVenis-DE": "云南金顺公司",
    "JeVenis-FR": "云南金顺公司",
    "JeVenis-IT": "云南金顺公司",
    "JeVenis-ES": "云南金顺公司",
    "JeVenis-UK": "云南金顺公司",
    "JeVenis-JP": "云南金顺公司",
    "JeVenis-AU": "云南金顺公司",
    "JeVenis-NL": "云南金顺公司",

    # LaVenty 系列 -> B账号账户管理
    "LaVenty-US": "B账号账户管理",
    "LaVenty-US-BR": "B账号账户管理",
    "LaVenty-CA": "B账号账户管理",
    "LaVenty-MX": "B账号账户管理",
    "LaVenty-NL": "B账号账户管理",
    "LaVenty-UK": "B账号账户管理",

    # Roaring 系列 -> C账号账户管理
    "Roaring-US": "C账号账户管理",
    "Roaring-BR": "C账号账户管理",
    "Roaring-CA": "C账号账户管理",
    "Roaring-MX": "C账号账户管理",
    "Roaring-NL": "C账号账户管理",
    "Roaring-DE": "C账号账户管理",

    # D-USA 系列 -> D账号账户管理
    "D-USA-US": "D账号账户管理",
    "D-USA-CA": "D账号账户管理",
    "D-USA-MX": "D账号账户管理",
    "D-USA-NL": "D账号账户管理",
    "D-USA-FR": "D账号账户管理",

    # E 系列 -> E账号账户管理
    "E-DE": "E账号账户管理",
    "E-IT": "E账号账户管理",
    "E-NL": "E账号账户管理",
    "E-CA": "E账号账户管理",
    "E-MX": "E账号账户管理",

    # F 系列 -> F账号账户管理
    "F-DE": "F账号账户管理",
    "F-FR": "F账号账户管理",
    "F-IT": "F账号账户管理",
    "F-ES": "F账号账户管理",
    "F-NL": "F账号账户管理",
    "F-CA": "F账号账户管理",
    "F-MX": "F账号账户管理",

    # G 系列 -> G站点紫鸟账号
    "G-USA": "G站点紫鸟账号",
    "G-CA": "G站点紫鸟账号",
    "G-MX": "G站点紫鸟账号",
    "G-NL": "G站点紫鸟账号",
}


def main():
    print("=" * 60)
    print("修复店铺名称")
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
        print("\n🔄 开始修复...")

        fixed_count = 0
        skipped_count = 0
        fix_details = []

        for store in stores:
            store_id, current_name, site, inventory_name = store

            if not inventory_name:
                skipped_count += 1
                print(f"   ⚠ ID {store_id}: 无 inventory_name，跳过")
                continue

            # 根据 inventory_name 获取正确的 name
            correct_name = STORE_NAME_FIX.get(inventory_name)

            if correct_name and current_name != correct_name:
                cursor.execute(
                    "UPDATE stores SET name = %s WHERE id = %s",
                    (correct_name, store_id)
                )
                fixed_count += 1
                fix_details.append({
                    "id": store_id,
                    "old_name": current_name,
                    "new_name": correct_name,
                    "inventory_name": inventory_name
                })
                print(f"   ✓ ID {store_id}: {current_name} -> {correct_name} (库存: {inventory_name})")
            else:
                skipped_count += 1
                print(f"   ⚠ ID {store_id}: {current_name} 无需修改")

        conn.commit()
        conn.close()

        print(f"\n📊 修复统计:")
        print(f"   成功: {fixed_count}")
        print(f"   跳过: {skipped_count}")

        # 保存修复详情
        if fix_details:
            report_file = f"store_name_fix_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            report_path = os.path.join(os.path.dirname(__file__), report_file)

            with open(report_path, 'w', encoding='utf-8') as f:
                json.dump(fix_details, f, ensure_ascii=False, indent=2)

            print(f"\n✅ 修复完成！报告已保存: {report_path}")
        else:
            print("\n✅ 无需修复")

    except Exception as e:
        print(f"\n❌ 修复失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
