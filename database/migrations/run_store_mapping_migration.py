#!/usr/bin/env python3
"""
店铺名映射数据库迁移脚本
执行步骤：
1. 备份现有数据
2. 添加 inventory_name 字段
3. 自动填充映射数据
4. 生成迁移报告
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import pymysql
from datetime import datetime
import json

# 数据库配置
DB_CONFIG = {
    'host': '115.190.250.14',
    'port': 3306,
    'user': 'bxhs_ai_assistance',
    'password': 'bxhsaiRoot@123',
    'database': 'bxhs_ai_assistance',
    'charset': 'utf8mb4'
}

# 店铺映射规则
STORE_MAPPING_RULES = {
    # 云南金顺公司
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

    # B账号
    ("B账号", "US"): "LaVenty-US",
    ("B账号", "BR"): "LaVenty-US-BR",

    # C账号
    ("C账号", "US"): "Roaring-US",
    ("C账号", "BR"): "Roaring-BR",

    # D账号
    ("D账号", "US"): "D-USA-US",
    ("D账号", "CA"): "D-USA-CA",

    # E账号
    ("E账号", "DE"): "E-DE",

    # F账号
    ("F账号", "DE"): "F-DE",
    ("F账号", "FR"): "F-FR",
    ("F账号", "IT"): "F-IT",
    ("F账号", "ES"): "F-ES",
    ("F账号", "NL"): "F-NL",
    ("F账号", "SE"): "F-SE",
    ("F账号", "PL"): "F-PL",
    ("F账号", "BE"): "F-BE",

    # G站点
    ("G站点", "US"): "G-USA",
    ("G站点", "CA"): "G-CA",
    ("G站点", "MX"): "G-MX",
}


def get_inventory_name(store_name: str, site: str) -> str:
    """根据店铺名和站点获取库存店铺名"""
    # 直接匹配
    for (pattern, site_code), inv_name in STORE_MAPPING_RULES.items():
        if pattern == store_name and site_code == site:
            return inv_name

    # 模糊匹配
    for (pattern, site_code), inv_name in STORE_MAPPING_RULES.items():
        if pattern in store_name and site_code == site:
            return inv_name

    return None


def backup_stores_table(conn):
    """备份 stores 表数据"""
    print("📦 正在备份 stores 表数据...")
    cursor = conn.cursor()

    # 查询现有数据（不查询 inventory_name，因为可能还不存在）
    cursor.execute("SELECT id, name, site FROM stores WHERE tenant_id = 1")
    stores = cursor.fetchall()

    # 生成备份文件
    backup_data = []
    for row in stores:
        backup_data.append({
            "id": row[0],
            "name": row[1],
            "site": row[2],
            "inventory_name": None
        })

    backup_file = f"stores_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    backup_path = os.path.join(os.path.dirname(__file__), backup_file)

    with open(backup_path, 'w', encoding='utf-8') as f:
        json.dump(backup_data, f, ensure_ascii=False, indent=2)

    print(f"✅ 备份完成: {backup_path}")
    print(f"   共备份 {len(backup_data)} 条记录")

    return backup_path, stores


def add_inventory_name_column(conn):
    """添加 inventory_name 字段"""
    print("\n🔧 正在添加 inventory_name 字段...")
    cursor = conn.cursor()

    try:
        # 检查字段是否已存在
        cursor.execute("""
            SELECT COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME = 'stores' AND COLUMN_NAME = 'inventory_name'
        """)

        if cursor.fetchone():
            print("⚠️ 字段已存在，跳过添加")
            return False

        # 添加字段
        cursor.execute("""
            ALTER TABLE stores
            ADD COLUMN inventory_name VARCHAR(100) DEFAULT NULL COMMENT '库存数据中的店铺名别名' AFTER department_id
        """)

        # 添加索引
        cursor.execute("""
            CREATE INDEX idx_inventory_name ON stores(inventory_name)
        """)

        conn.commit()
        print("✅ 字段添加成功")
        return True

    except Exception as e:
        conn.rollback()
        print(f"❌ 添加字段失败: {e}")
        raise


def update_store_mappings(conn, stores):
    """更新店铺映射数据"""
    print("\n🔄 正在更新店铺映射数据...")
    cursor = conn.cursor()

    updated_count = 0
    skipped_count = 0
    failed_count = 0
    update_details = []

    for store in stores:
        if len(store) == 4:
            store_id, name, site, current_inv_name = store
        else:
            store_id, name, site = store
            current_inv_name = None

        # 如果已经有 inventory_name，跳过
        if current_inv_name:
            skipped_count += 1
            continue

        # 计算映射值
        inventory_name = get_inventory_name(name, site)

        if inventory_name:
            try:
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
            except Exception as e:
                failed_count += 1
                print(f"   ✗ {name} ({site}) 更新失败: {e}")
        else:
            skipped_count += 1
            print(f"   ⚠ {name} ({site}) 未找到匹配规则，跳过")

    conn.commit()

    print(f"\n📊 更新统计:")
    print(f"   成功: {updated_count}")
    print(f"   跳过: {skipped_count}")
    print(f"   失败: {failed_count}")

    return update_details


def generate_report(backup_path, update_details):
    """生成迁移报告"""
    print("\n📝 生成迁移报告...")

    report = {
        "migration_time": datetime.now().isoformat(),
        "backup_file": backup_path,
        "updated_stores": update_details,
        "total_updated": len(update_details)
    }

    report_file = f"migration_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    report_path = os.path.join(os.path.dirname(__file__), report_file)

    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"✅ 报告已生成: {report_path}")
    return report_path


def create_rollback_script(backup_path):
    """创建回滚脚本"""
    print("\n🔙 创建回滚脚本...")

    rollback_script = f'''#!/usr/bin/env python3
"""
店铺名映射回滚脚本
用于撤销迁移操作
"""
import pymysql
import json

DB_CONFIG = {{
    'host': '115.190.250.14',
    'port': 3306,
    'user': 'bxhs_ai_assistance',
    'password': 'bxhsaiRoot@123',
    'database': 'bxhs_ai_assistance',
    'charset': 'utf8mb4'
}}

def rollback():
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()
    
    print("🔄 开始回滚...")
    
    # 1. 清空 inventory_name 字段
    cursor.execute("UPDATE stores SET inventory_name = NULL")
    print("✅ 已清空 inventory_name 字段")
    
    # 2. 或者删除字段（如果需要完全回滚）
    # cursor.execute("ALTER TABLE stores DROP COLUMN inventory_name")
    # print("✅ 已删除 inventory_name 字段")
    
    conn.commit()
    conn.close()
    print("✅ 回滚完成")

if __name__ == "__main__":
    rollback()
'''

    rollback_file = f"rollback_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py"
    rollback_path = os.path.join(os.path.dirname(__file__), rollback_file)

    with open(rollback_path, 'w', encoding='utf-8') as f:
        f.write(rollback_script)

    print(f"✅ 回滚脚本已生成: {rollback_path}")
    return rollback_path


def main():
    """主函数"""
    print("=" * 60)
    print("店铺名映射数据库迁移")
    print("=" * 60)

    try:
        # 连接数据库
        print("\n🔗 连接数据库...")
        conn = pymysql.connect(**DB_CONFIG)
        print("✅ 数据库连接成功")

        # 1. 备份数据
        backup_path, stores = backup_stores_table(conn)

        # 2. 添加字段
        add_inventory_name_column(conn)

        # 3. 更新映射（重新查询数据，包含新字段）
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, site, inventory_name FROM stores WHERE tenant_id = 1")
        stores_with_inv = cursor.fetchall()
        update_details = update_store_mappings(conn, stores_with_inv)

        # 4. 生成报告
        report_path = generate_report(backup_path, update_details)

        # 5. 创建回滚脚本
        rollback_path = create_rollback_script(backup_path)

        conn.close()

        print("\n" + "=" * 60)
        print("✅ 迁移完成！")
        print("=" * 60)
        print(f"\n📁 生成的文件:")
        print(f"   备份文件: {backup_path}")
        print(f"   迁移报告: {report_path}")
        print(f"   回滚脚本: {rollback_path}")
        print(f"\n📊 更新统计: 共更新 {len(update_details)} 个店铺映射")
        print("\n⚠️  如果需要回滚，请运行:")
        print(f"   python {rollback_path}")

    except Exception as e:
        print(f"\n❌ 迁移失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
