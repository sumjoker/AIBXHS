
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database.database import SessionLocal, engine
import sqlalchemy as sa

def check_columns():
    try:
        db = SessionLocal()
        inspect = sa.inspect(engine)
        columns = inspect.get_columns("email_messages")
        print("email_messages表字段:")
        for col in columns:
            print(f"  - {col['name']}: {col['type']}")
        db.close()
    except Exception as e:
        print(f"错误: {e}")

if __name__ == "__main__":
    check_columns()
