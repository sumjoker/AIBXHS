import sys
import os
import io
import logging
import platform

# ========== Windows 控制台 UTF-8 编码修复 ==========
if "pytest" not in sys.modules:
    if platform.system() == 'Windows':
        os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
        # 设置控制台代码页为 UTF-8
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
        # 重建 stdout/stderr 为 UTF-8
        if hasattr(sys.stdout, 'buffer'):
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        if hasattr(sys.stderr, 'buffer'):
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    else:
        if hasattr(sys.stdout, 'buffer'):
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        if hasattr(sys.stderr, 'buffer'):
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# 配置日志 - 使用 UTF-8 编码的文件 handler 避免控制台乱码
log_formatter = logging.Formatter(
    fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# 控制台 handler
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)
console_handler.setLevel(logging.INFO)

# 根日志配置
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.handlers.clear()
root_logger.addHandler(console_handler)

logger = logging.getLogger(__name__)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

from routers import inventory, reviews, dashboard, chat, auth, restock, departments, notifications, stores, products, tenants, local_inventory, business_settings, store_mapping, emails

from config import get_settings

settings = get_settings()

app = FastAPI(
    title="宝鑫华盛AI助手",
    description="跨境电商AI运营平台后端API",
    version="1.0.0"
)

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(inventory.router, prefix="/api")
app.include_router(reviews.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(restock.router, prefix="/api")
app.include_router(chat.router)
app.include_router(auth.router)
app.include_router(departments.router)
app.include_router(notifications.router)
app.include_router(stores.router)
app.include_router(products.router)
app.include_router(tenants.router)
app.include_router(emails.router, prefix="/api")
app.include_router(local_inventory.router, prefix="/api")
app.include_router(business_settings.router)
app.include_router(store_mapping.router, prefix="/api")

@app.get("/api/health")
async def health_check():
    return {
        "status": "ok",
        "message": "宝鑫华盛AI助手服务运行正常",
        "version": "1.0.0"
    }

@app.get("/api/test-push-notifications")
async def test_push_notifications():
    """手动触发差评通知推送测试"""
    try:
        from services.scheduler import push_daily_review_notifications_job
        logger.info("手动触发差评通知推送测试...")
        push_daily_review_notifications_job()
        return {
            "success": True,
            "message": "测试推送已执行，请查看后端控制台日志"
        }
    except Exception as e:
        logger.error(f"测试推送失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {
            "success": False,
            "message": f"测试推送失败: {str(e)}"
        }

@app.get("/api/clear-today-notifications")
async def clear_today_notifications():
    """清除今日所有差评通知（用于测试）"""
    from database.database import SessionLocal
    from sqlalchemy import text
    from datetime import date
    
    try:
        today = date.today().isoformat()
        db = SessionLocal()
        result = db.execute(text("""
            DELETE FROM notifications 
            WHERE type = 'warning' 
              AND title LIKE '%未处理差评提醒%'
              AND DATE(created_at) = :today
        """), {"today": today})
        deleted_count = result.rowcount
        db.commit()
        db.close()
        
        logger.info(f"清除了今天 {deleted_count} 条差评通知")
        return {
            "success": True,
            "message": f"已清除今天 {deleted_count} 条差评通知"
        }
    except Exception as e:
        logger.error(f"清除通知失败: {e}")
        return {
            "success": False,
            "message": f"清除通知失败: {str(e)}"
        }

@app.on_event("startup")
async def startup_event():
    """应用启动事件"""
    logger.info("宝鑫华盛AI助手后端服务启动中...")
    try:
        from database.database import init_db
        init_db()
        logger.info("数据库初始化完成")
    except Exception as e:
        logger.error(f"数据库初始化失败: {e}")
        logger.info("使用现有数据库表结构")
    
    try:
        from services.scheduler import init_scheduler
        init_scheduler()
        logger.info("定时任务调度器已启动")
    except Exception as e:
        logger.error(f"定时任务调度器启动失败: {e}")

@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭事件"""
    logger.info("服务已关闭")

@app.get("/")
async def root():
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    index_path = os.path.join(static_dir, "index.html")
    
    if os.path.exists(index_path):
        return FileResponse(index_path)
    
    return {
        "message": "欢迎使用宝鑫华盛AI助手API",
        "docs": "/docs",
        "health": "/api/health"
    }

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

if __name__ == "__main__":
    import uvicorn
    from config import get_settings
    settings = get_settings()
    
    # Windows 不支持多 worker 的 multiprocessing spawn 模式，强制使用 1
    workers = 1 if platform.system() == 'Windows' else 4
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.PORT,
        reload=False,
        log_level="info",
        workers=workers,
        limit_concurrency=1000,
        timeout_keep_alive=5
    )
