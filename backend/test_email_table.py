import sys
sys.path.insert(0, 'e:\\AIbxhs\\backend')

from database.database import SessionLocal
from sqlalchemy import text

db = SessionLocal()

try:
    print("检查 email_messages 表是否存在...")
    result = db.execute(text("SHOW TABLES LIKE 'email_messages'"))
    table_exists = result.fetchone()
    
    if table_exists:
        print("✓ email_messages 表存在")
        
        print("\n检查表结构...")
        result = db.execute(text("DESCRIBE email_messages"))
        columns = result.fetchall()
        
        print("表字段:")
        for col in columns:
            print(f"  - {col[0]} ({col[1]})")
        
        print("\n检查数据数量...")
        result = db.execute(text("SELECT COUNT(*) FROM email_messages"))
        count = result.scalar()
        print(f"✓ 共有 {count} 条记录")
        
        if count > 0:
            print("\n查看前3条数据...")
            result = db.execute(text("SELECT * FROM email_messages LIMIT 3"))
            rows = result.fetchall()
            for i, row in enumerate(rows, 1):
                print(f"\n记录 {i}:")
                for j, val in enumerate(row):
                    print(f"  字段{j}: {val}")
    else:
        print("✗ email_messages 表不存在")
        print("\n当前数据库中的所有表:")
        result = db.execute(text("SHOW TABLES"))
        tables = result.fetchall()
        for table in tables:
            print(f"  - {table[0]}")
            
except Exception as e:
    print(f"错误: {e}")
    import traceback
    traceback.print_exc()
finally:
    db.close()