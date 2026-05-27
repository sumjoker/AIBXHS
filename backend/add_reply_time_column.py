
from database.database import SessionLocal, engine
from sqlalchemy import text

def add_column():
    try:
        db = SessionLocal()
        
        # 检查字段是否已存在
        check_column = db.execute(text("""
            SHOW COLUMNS FROM email_messages LIKE 'reply_text_time'
        """)).fetchone()
        
        if check_column:
            print("字段 reply_text_time 已存在")
            db.close()
            return
        
        # 添加字段
        db.execute(text("""
            ALTER TABLE email_messages 
            ADD COLUMN reply_text_time DATETIME DEFAULT NULL 
            COMMENT '提交回复时间'
        """))
        db.commit()
        print("字段 reply_text_time 添加成功")
        
        db.close()
    except Exception as e:
        print(f"错误: {e}")

if __name__ == "__main__":
    add_column()
