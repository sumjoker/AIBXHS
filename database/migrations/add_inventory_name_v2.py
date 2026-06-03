#!/usr/bin/env python3
"""
添加 inventory_name 字段并根据库存数据填充
支持回滚
"""
import sys
import os
import pymysql
import json
from datetime import datetime

DB_CONFIG = {
    'host': '115.190.250.14',
    'port': 3306,
    'user': 'bxhs_ai_assistance',
    'password': 'bxhsaiRoot@123',
    'database': 'bxhs_ai_assistance',
    'charset': 'utf8mb4'
}

# 站点代码映射
SITE_CODE = {
    "美国": "US",
    "英国": "UK",
    "德国": "DE",
    "法国": "FR",
    "意大利": "IT",
    "西班牙": "ES",
    "荷兰": "NL",
    "加拿大": "CA",
    "墨西哥": "MX",
    "巴西": "BR",
    "日本": "JP",
    "澳大利亚": "AU",
    "瑞典": "SE",
    "波兰": "PL",
    "比利时": "BE",
    "爱尔兰": "IE",
    "阿联酋": "AE",
}

# 店铺格式规则
# 格式1: 直接跟国家代码 (如 G-CA, JeVenis-US)
# 格式2: 区域-国家代码 (如 D-USA-US, D-EU-DE)
STORE_FORMAT = {
    "云南金顺公司": {"prefix": "JeVenis-", "use_region": False},
    "B账号账户管理": {"prefix": "LaVenty-", "use_region": True},
    "C账号账户管理": {"prefix": "Roaring-", "use_region": True},
    "D账号账户管理": {"prefix": "D-", "use_region": True},
    "E账号账户管理": {"prefix": "E-", "use_region": True},
    "F账号账户管理": {"prefix": "F-", "use_region": True},
    "H站点账户账号": {"prefix": "H-", "use_region": False},
    "G站点紫鸟账号": {"prefix": "G-", "use_region": False},
}

# 区域前缀映射
REGION_PREFIX = {
    "美国": "USA-",
    "加拿大": "USA-",
    "墨西哥": "USA-",
    "英国": "UK-",
    "德国": "EU-",
    "法国": "EU-",
    "意大利": "EU-",
    "西班牙": "EU-",
    "荷兰": "EU-",
    "瑞典": "EU-",
    "波兰": "EU-",
    "比利时": "EU-",
    "爱尔兰": "EU-",
    "巴西": "BR-",
    "日本": "JP-",
    "澳大利亚": "AU-",
    "阿联酋": "AE-",
}


def backup_stores():
    """备份当前 stores 表数据"""
    print("📦 备份 stores 表数据...")
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, site, department_id FROM stores WHERE tenant_id = 1 ORDER BY id")
    stores = cursor.fetchall()
    conn.close()

    backup_data = []
    for row in stores:
        backup_data.append({
            "id": row[0],
            "name": row[1],
            "site": row[2],
            "department_id": row[3]
        })

    backup_file = f"stores_backup_before_inv_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    backup_path = os.path.join(os.path.dirname(__file__), backup_file)
    with open(backup_path, 'w', encoding='utf-8') as f:
        json.dump(backup_data, f, ensure_ascii=False, indent=2)

    print(f"   ✅ 备份完成: {backup_path}")
    print(f"   📊 共备份 {len(backup_data)} 条记录")
    return backup_path, stores


def analyze_inventory_accounts():
    """分析库存表中的 account 字段格式"""
    print("\n🔍 分析库存表 account 字段...")
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()

    # 获取所有不同的 account 值
    cursor.execute("SELECT DISTINCT account FROM inventory_snapshots WHERE account IS NOT NULL AND account != '' ORDER BY account LIMIT 50")
    accounts = [row[0] for row in cursor.fetchall()]
    conn.close()

    print(f"   找到 {len(accounts)} 种不同的 account 格式")
    print("\n   示例:")
    for acc in accounts[:15]:
        print(f"     - {acc}")

    return accounts


def add_inventory_name_column():
    """添加 inventory_name 字段"""
    print("\n🔧 添加 inventory_name 字段...")
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()

    try:
        # 检查字段是否已存在
        cursor.execute("""
            SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_NAME='stores' AND COLUMN_NAME='inventory_name'
        """)
        if cursor.fetchone():
            print("   ⚠️ 字段已存在，跳过添加")
            conn.close()
            return

        # 添加字段
        cursor.execute("""
            ALTER TABLE stores
            ADD COLUMN inventory_name VARCHAR(100) DEFAULT NULL COMMENT '库存数据中的店铺名别名'
        """)
        cursor.execute("""
            CREATE INDEX idx_inventory_name ON stores(inventory_name)
        """)
        conn.commit()
        print("   ✅ 字段添加成功")
    except Exception as e:
        print(f"   ⚠️ {e}")
    finally:
        conn.close()


def generate_inventory_name(name, site):
    """根据 name 和 site 生成 inventory_name"""
    config = STORE_FORMAT.get(name)
    if not config:
        return None

    prefix = config["prefix"]
    use_region = config["use_region"]
    code = SITE_CODE.get(site)

    if not code:
        return None

    if use_region:
        region = REGION_PREFIX.get(site)
        if region:
            return f"{prefix}{region}{code}"
        return f"{prefix}{code}"
    else:
        return f"{prefix}{code}"


def fill_inventory_name(stores):
    """填充 inventory_name 字段"""
    print("\n📝 填充 inventory_name 字段...")
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()

    updated = 0
    skipped = 0
    details = []

    for store in stores:
        store_id, name, site, dept_id = store
        inv_name = generate_inventory_name(name, site)

        if inv_name:
            cursor.execute(
                "UPDATE stores SET inventory_name = %s WHERE id = %s",
                (inv_name, store_id)
            )
            updated += 1
            details.append({"id": store_id, "name": name, "site": site, "inventory_name": inv_name})
            print(f"   ID:{store_id:2d} | {name:20s} | {site:8s} -> {inv_name}")
        else:
            skipped += 1
            print(f"   ⚠️ ID:{store_id:2d} | {name:20s} | {site:8s} | 未找到映射规则，跳过")

    conn.commit()
    conn.close()

    print(f"\n   ✅ 更新: {updated}, 跳过: {skipped}")
    return details


def create_rollback_script(backup_path):
    """创建回滚脚本"""
    print("\n🔙 创建回滚脚本...")

    rollback_content = f'''#!/usr/bin/env python3
"""
回滚脚本：删除 inventory_name 字段
备份文件: {backup_path}
"""
import pymysql

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

    # 1. 清空 inventory_name
    cursor.execute("UPDATE stores SET inventory_name = NULL")
    print("   ✅ 已清空 inventory_name 字段")

    # 2. 删除字段（可选）
    # cursor.execute("ALTER TABLE stores DROP COLUMN inventory_name")
    # print("   ✅ 已删除 inventory_name 字段")

    conn.commit()
    conn.close()
    print("✅ 回滚完成")

if __name__ == "__main__":
    rollback()
'''

    rollback_file = f"rollback_inventory_name_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py"
    rollback_path = os.path.join(os.path.dirname(__file__), rollback_file)

    with open(rollback_path, 'w', encoding='utf-8') as f:
        f.write(rollback_content)

    print(f"   ✅ 回滚脚本: {rollback_path}")
    return rollback_path


def main():
    print("=" * 70)
    print("添加 inventory_name 字段并填充")
    print("=" * 70)

    # 1. 备份
    backup_path, stores = backup_stores()

    # 2. 分析库存数据
    accounts = analyze_inventory_accounts()

    # 3. 添加字段
    add_inventory_name_column()

    # 4. 填充数据
    details = fill_inventory_name(stores)

    # 5. 创建回滚脚本
    rollback_path = create_rollback_script(backup_path)

    # 6. 保存报告
    report = {
        "time": datetime.now().isoformat(),
        "backup_file": backup_path,
        "rollback_script": rollback_path,
        "total_stores": len(stores),
        "updated": len(details),
        "details": details
    }
    report_file = f"inventory_name_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    report_path = os.path.join(os.path.dirname(__file__), report_file)
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 70)
    print("✅ 完成!")
    print("=" * 70)
    print(f"\n📁 文件:")
    print(f"   备份: {backup_path}")
    print(f"   报告: {report_path}")
    print(f"   回滚: {rollback_path}")
    print(f"\n📊 统计: 共 {len(stores)} 个店铺, 更新 {len(details)} 个")
    print(f"\n⚠️  如需回滚，运行: python {rollback_path}")


if __name__ == "__main__":
    main()
