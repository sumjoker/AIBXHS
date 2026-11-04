import sys
import os
import io
import time
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

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
import os

from routers import inventory, reviews, dashboard, chat, auth, restock, departments, notifications, stores, products, tenants, store_groups, inbound, outbound, purchase, inventory_batch, operation_logs, permissions, warehouses, stock_transfer, local_inventory, business_settings, store_mapping, emails, inventory_count, product_bindings, ads, ad_rules, ad_suggestions, ad_execution_logs, replenishment, shipments, data_warnings, product_sales, threshold_settings, product_page_info, suppliers, upload, product_selection, product_aging, product_buybox, product_shipment_notice, group_messages, base_table, ai_creation, product_development, video_bindings, ai_image, tags
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
app.include_router(tags.router, prefix="/api")
app.include_router(chat.router)
app.include_router(auth.router)
app.include_router(departments.router)
app.include_router(notifications.router)
app.include_router(stores.router)
app.include_router(store_groups.router)
app.include_router(products.router)
app.include_router(tenants.router)
app.include_router(product_selection.router)
app.include_router(emails.router, prefix="/api")
app.include_router(data_warnings.router, prefix="/api")
app.include_router(product_sales.router, prefix="/api")
app.include_router(threshold_settings.router, prefix="/api")
app.include_router(local_inventory.router, prefix="/api")
app.include_router(business_settings.router)
app.include_router(store_mapping.router, prefix="/api")
app.include_router(inbound.router)
app.include_router(outbound.router)
app.include_router(purchase.router)
app.include_router(inventory_batch.router)
app.include_router(operation_logs.router)
app.include_router(permissions.router)
app.include_router(stock_transfer.router)
app.include_router(warehouses.router)
app.include_router(product_page_info.router)

app.include_router(inventory_count.router)

app.include_router(product_bindings.router)
app.include_router(replenishment.router)
app.include_router(shipments.router)
app.include_router(suppliers.router)
app.include_router(upload.router)
app.include_router(ai_creation.router)
app.include_router(video_bindings.router)
app.include_router(ai_image.router)
app.include_router(base_table.router)

app.include_router(ads.router, prefix="/api")
app.include_router(ad_rules.router, prefix="/api")
app.include_router(ad_suggestions.router, prefix="/api")
app.include_router(ad_execution_logs.router, prefix="/api")
app.include_router(product_aging.router, prefix="/api")
app.include_router(product_buybox.router, prefix="/api")
app.include_router(product_shipment_notice.router, prefix="/api")
app.include_router(group_messages.router, prefix="/api")
app.include_router(product_development.router, prefix="/api")

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
        from config import get_settings
        if not get_settings().H3_BASE_URL:
            logger.warning(
                "H3_BASE_URL 未配置（为空），AI视频/高清处理的 ComfyUI 任务将无法执行或查询，"
                "请检查 backend/.env 的 H3_BASE_URL/H3_AUTH_USER/H3_AUTH_PASSWORD 配置"
            )
    except Exception:
        pass
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

    try:
        from services.h3_video_service import resume_pending_tasks
        resumed = resume_pending_tasks()
        if resumed is None:
            logger.error("H3 视频未完成任务恢复已跳过（H3_BASE_URL 未配置）")
        else:
            logger.info(f"H3 视频未完成任务已恢复监听（{resumed} 条）")
    except Exception as e:
        logger.error(f"H3 视频任务恢复失败: {e}")

    try:
        from services.upscale_service import resume_pending_tasks as resume_upscale_tasks
        resumed_upscale = resume_upscale_tasks()
        if resumed_upscale is None:
            logger.error("超分未完成任务恢复已跳过（H3_BASE_URL 未配置）")
        else:
            logger.info(f"超分未完成任务已恢复监听（{resumed_upscale} 条）")
    except Exception as e:
        logger.error(f"超分任务恢复失败: {e}")

    try:
        from services.ai_image_service import resume_pending_tasks as resume_image_tasks
        resume_image_tasks()
        logger.info("AI 生图未完成任务已恢复监听")
    except Exception as e:
        logger.error(f"AI 生图任务恢复失败: {e}")

    try:
        import asyncio as _asyncio
        from routers.product_development import auto_migrate_designer_images
        _asyncio.create_task(auto_migrate_designer_images())
        logger.info("美工图片 TOS 自动迁移任务已启动")
    except Exception as e:
        logger.error(f"启动美工图片自动迁移任务失败: {e}")

@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭事件"""
    logger.info("服务已关闭")

static_dir = os.path.join(os.path.dirname(__file__), "static")

# SPA 不需要缓存的入口文件
_NO_CACHE_EXTS = {".html", ".htm"}

# 静态资源扩展名：未命中时返回 404，不回退 index.html（见下方 SPA fallback 注释）
_STATIC_EXTS = {
    ".js", ".mjs", ".cjs", ".css", ".map",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".bmp",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp4", ".webm", ".mp3", ".wav",
    ".json", ".txt", ".xml", ".pdf",
}

@app.middleware("http")
async def serve_static_middleware(request: Request, call_next):
    path = request.url.path
    
    # /openapi.json 必须放行：它是 Swagger/ReDoc 的数据源。若交给下方静态
    # 处理逻辑，文件不存在会命中 SPA fallback 返回 index.html（HTML），
    # Swagger UI 解析失败并报 “does not specify a valid version field”。
    if path.startswith(("/api/", "/docs", "/redoc", "/openapi.json")):
        return await call_next(request)
    
    file_path = os.path.join(static_dir, path.lstrip("/"))
    
    if os.path.isfile(file_path):
        # 判断是否带 hash（Vite 输出文件名都带 hash，如 index-abc123.js）
        is_hashed = "-" in os.path.basename(path) and os.path.splitext(path)[1] in (".js", ".css", ".png", ".jpg", ".svg", ".woff2", ".woff", ".ttf")
        ext = os.path.splitext(path)[1].lower()

        if ext in _NO_CACHE_EXTS:
            # index.html / SPA 入口：每次都拉最新
            response = FileResponse(file_path)
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            return response
        elif is_hashed:
            # 带 hash 的产物：内容不变 URL 不变，缓存 1 年
            response = FileResponse(file_path)
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            return response
        else:
            return FileResponse(file_path)
    
    index_path = os.path.join(static_dir, "index.html")

    # 静态资源未命中必须返回 404，绝不能回退 index.html：
    # 浏览器对 <script type="module"> 强制校验 MIME，拿到 HTML 会报
    # "Failed to load module script ... MIME type of text/html" 并整页白屏
    # （典型场景：浏览器缓存了旧 index.html，引用了已删除的旧 hash 产物）。
    ext = os.path.splitext(path)[1].lower()
    if path.startswith("/assets/") or ext in _STATIC_EXTS:
        return JSONResponse(
            {"detail": "Not Found", "path": path},
            status_code=404,
            headers={"Cache-Control": "no-store"},
        )

    if os.path.isfile(index_path):
        # SPA fallback 也强制 no-cache
        response = FileResponse(index_path)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response
    
    return await call_next(request)

# 请求日志中间件：为每个 API 请求记录发起用户（从 JWT 解码，不查数据库）
@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    path = request.url.path
    if path == "/api/health":
        return await call_next(request)

    user_desc = "匿名"
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            from jose import jwt
            payload = jwt.decode(auth[7:].strip(), settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            user_desc = f"用户 {payload.get('sub') or payload.get('uid')}(ID:{payload.get('uid')})"
        except Exception:
            user_desc = "无效token"

    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        elapsed = (time.perf_counter() - start) * 1000
        logger.error(f"{user_desc} {request.method} {path} 500 - {elapsed:.0f}ms")
        raise
    elapsed = (time.perf_counter() - start) * 1000
    logger.info(f"{user_desc} {request.method} {path} {response.status_code} - {elapsed:.0f}ms")
    return response

if __name__ == "__main__":
    import uvicorn
    from config import get_settings
    settings = get_settings()
    
    # 强制单进程模式（项目约定）：多 worker 会并发执行建表/列补丁、定时任务×N 重复运行、
    # 视频任务 resume 竞态（同一成片被多进程重复上传 TOS）、日志成对出现等问题
    workers = 1
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.PORT,
        reload=False,
        log_level="info",
        access_log=False,
        workers=workers,
        limit_concurrency=1000,
        timeout_keep_alive=5
    )
