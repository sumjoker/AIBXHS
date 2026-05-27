import sys
sys.path.insert(0, 'e:\\AIbxhs\\backend')

from database.database import SessionLocal
from sqlalchemy import text

db = SessionLocal()

try:
    print("Checking email_messages table...")
    result = db.execute(text("SHOW TABLES LIKE 'email_messages'"))
    table_exists = result.fetchone()
    
    if table_exists:
        print("OK: email_messages table exists")
        
        print("\nTable structure:")
        result = db.execute(text("DESCRIBE email_messages"))
        columns = result.fetchall()
        
        for col in columns:
            print(f"  - {col[0]} ({col[1]})")
        
        print("\nChecking data count...")
        result = db.execute(text("SELECT COUNT(*) FROM email_messages"))
        count = result.scalar()
        print(f"OK: Total {count} records")
        
        if count > 0:
            print("\nFirst 3 records:")
            result = db.execute(text("SELECT * FROM email_messages LIMIT 3"))
            rows = result.fetchall()
            for i, row in enumerate(rows, 1):
                print(f"\nRecord {i}:")
                for j, val in enumerate(row):
                    print(f"  Column{j}: {val}")
    else:
        print("ERROR: email_messages table NOT found")
        print("\nCurrent tables in database:")
        result = db.execute(text("SHOW TABLES"))
        tables = result.fetchall()
        for table in tables:
            print(f"  - {table[0]}")
            
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
finally:
    db.close()