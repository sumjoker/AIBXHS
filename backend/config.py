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
        """构建数据库连接URL"""
        # URL编码密码，处理特殊字符
        encoded_password = urllib.parse.quote_plus(self.DB_PASSWORD)
        return f"mysql+pymysql://{self.DB_USER}:{encoded_password}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}?charset=utf8mb4"
    
    class Config:
        env_file = os.path.join(os.path.dirname(__file__), ".env")


def get_settings() -> Settings:
    return Settings()
