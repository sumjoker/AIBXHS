import sys
sys.path.insert(0, 'e:\\AIbxhs\\backend')

from database.database import SessionLocal
from sqlalchemy import text

db = SessionLocal()

try:
    query = text("""
        SELECT
            id,
            tenant_id,
            store_id,
            site,
            language,
            mail_subject,
            mail_content,
            mail_content_chinese,
            buyer_mail_number,
            ai_reply_content,
            reply_date
        FROM email_messages
        LIMIT 3
    """)
    
    result = db.execute(query)
    emails = result.fetchall()
    
    print(f"Found {len(emails)} emails")
    for email in emails:
        print(f"\nID: {email[0]}")
        print(f"Subject: {email[5]}")
        print(f"Reply date: {email[10]}")
        print(f"Type of reply_date: {type(email[10])}")
        
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
finally:
    db.close()
