from pydantic_settings import BaseSettings
from functools import lru_cache
import urllib.parse
import os


class Settings(BaseSettings):
    """应用配置"""
    PORT: int = 8000
    
    # 数据库配置
    DB_HOST: str = "localhost"
    DB_PORT: int = 3306
    DB_USER: str = "root"
    DB_PASSWORD: str = "Root@123456"
    DB_NAME: str = "baoxinhuasheng"

    # Demo 模式配置
    DEMO_MODE: bool = False
    DEMO_DB_HOST: str = "localhost"
    DEMO_DB_PORT: int = 3306
    DEMO_DB_USER: str = "root"
    DEMO_DB_PASSWORD: str = "123456"
    DEMO_DB_NAME: str = "bxhs_ai_assistance_demo"
    
    # 飞书配置
    FEISHU_APP_ID: str = ""
    FEISHU_APP_SECRET: str = ""
    FEISHU_INVENTORY_BASE_TOKEN: str = ""
    FEISHU_INVENTORY_TABLE_ID: str = ""
    FEISHU_REVIEW_BASE_TOKEN: str = ""
    FEISHU_REVIEW_TABLE_ID: str = ""
    
    # OpenAI配置
    OPENAI_API_KEY: str = ""
    OPENAI_API_BASE: str = "https://yunwu.ai/v1"
    OPENAI_MODEL: str = "deepseek-v4-flash"
    
    # JWT配置
    SECRET_KEY: str = "your-secret-key-change-this-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7
    
    @property
    def DATABASE_URL(self) -> str:
        """构建数据库连接URL，DEMO_MODE=true 时连接本地 Demo 库"""
        if self.DEMO_MODE:
            host = self.DEMO_DB_HOST
            port = self.DEMO_DB_PORT
            user = self.DEMO_DB_USER
            pwd = self.DEMO_DB_PASSWORD
            db = self.DEMO_DB_NAME
        else:
            host = self.DB_HOST
            port = self.DB_PORT
            user = self.DB_USER
            pwd = self.DB_PASSWORD
            db = self.DB_NAME
        encoded_password = urllib.parse.quote_plus(pwd)
        return f"mysql+pymysql://{user}:{encoded_password}@{host}:{port}/{db}?charset=utf8mb4"
    
    class Config:
        env_file = os.path.join(os.path.dirname(__file__), ".env")


def get_settings() -> Settings:
    return Settings()
