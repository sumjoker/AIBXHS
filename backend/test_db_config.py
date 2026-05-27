import sys
sys.path.insert(0, 'e:\\AIbxhs\\backend')

from config import get_settings

settings = get_settings()

print("数据库配置:")
print(f"  DB_HOST: {settings.DB_HOST}")
print(f"  DB_PORT: {settings.DB_PORT}")
print(f"  DB_USER: {settings.DB_USER}")
print(f"  DB_NAME: {settings.DB_NAME}")
print(f"  DATABASE_URL: {settings.DATABASE_URL}")