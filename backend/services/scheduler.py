from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime, timedelta
import logging
import time

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()

def acquire_distributed_lock(db, lock_key: str, timeout_minutes: int = 30) -> bool:
    """获取分布式锁，防止多进程同时执行
    返回 True 表示成功获取锁，可以执行任务
    """
    from sqlalchemy import text
    lock_expires_at = datetime.utcnow() + timedelta(minutes=timeout_minutes)
    
    try:
        # 先检查锁表是否存在，不存在直接返回 True（允许执行）
        check_table_sql = text("SHOW TABLES LIKE 'scheduler_locks'")
        table_exists = db.execute(check_table_sql).fetchone() is not None
        if not table_exists:
            logger.warning(f"scheduler_locks 表不存在，不使用锁: {lock_key}")
            return True
        
        # 尝试获取锁：插入记录
        insert_sql = text("""
            INSERT INTO scheduler_locks (lock_key, acquired_at, expires_at, is_active)
            VALUES (:key, NOW(), :expires, TRUE)
            ON DUPLICATE KEY UPDATE
                acquired_at = IF(is_active = FALSE OR expires_at < NOW(), NOW(), acquired_at),
                expires_at = IF(is_active = FALSE OR expires_at < NOW(), :expires, expires_at),
                is_active = IF(is_active = FALSE OR expires_at < NOW(), TRUE, is_active)
        """)
        result = db.execute(insert_sql, {"key": lock_key, "expires": lock_expires_at})
        
        # 检查是否真的获取到了锁（影响行数 > 0 说明是第一个）
        if result.rowcount > 0:
            db.commit()
            logger.info(f"✅ 成功获取分布式锁: {lock_key}")
            return True
        
        # 如果没有插入行，检查是否是锁已过期被我们自动续期了
        check_sql = text("""
            SELECT 1 FROM scheduler_locks
            WHERE lock_key = :key
              AND is_active = TRUE
              AND expires_at > NOW()
        """)
        check_result = db.execute(check_sql, {"key": lock_key}).fetchone()
        if not check_result:
            # 锁已过期，我们更新锁
            update_sql = text("""
                UPDATE scheduler_locks
                SET is_active = TRUE,
                    acquired_at = NOW(),
                    expires_at = :expires
                WHERE lock_key = :key
            """)
            db.execute(update_sql, {"key": lock_key, "expires": lock_expires_at})
            db.commit()
            logger.info(f"🔄 锁已过期，重新获取: {lock_key}")
            return True
        
        db.commit()
        logger.info(f"🔒 其他进程正在执行该任务，跳过: {lock_key}")
        return False
        
    except Exception as e:
        db.rollback()
        logger.error(f"获取分布式锁失败: {e}")
        # 出错时允许执行任务（不阻塞）
        return True


def release_distributed_lock(db, lock_key: str):
    """释放分布式锁"""
    from sqlalchemy import text
    try:
        # 检查锁表是否存在
        check_table_sql = text("SHOW TABLES LIKE 'scheduler_locks'")
        table_exists = db.execute(check_table_sql).fetchone() is not None
        if not table_exists:
            return
        
        update_sql = text("""
            UPDATE scheduler_locks
            SET is_active = FALSE
            WHERE lock_key = :key
        """)
        db.execute(update_sql, {"key": lock_key})
        db.commit()
        logger.info(f"🔓 释放分布式锁: {lock_key}")
    except Exception as e:
        logger.error(f"释放分布式锁失败: {e}")


def init_scheduler():
    scheduler.add_job(
        check_inventory_job,
        trigger=IntervalTrigger(hours=1),
        id="inventory_check",
        name="库存检查任务",
        replace_existing=True
    )

    scheduler.add_job(
        check_reviews_job,
        trigger=IntervalTrigger(minutes=30),
        id="reviews_check",
        name="差评监控任务",
        replace_existing=True
    )

    scheduler.add_job(
        send_daily_report_job,
        trigger="cron",
        hour=9,
        minute=0,
        id="daily_report",
        name="每日运营报告",
        replace_existing=True
    )

    scheduler.add_job(
        translate_untranslated_reviews_job,
        trigger="cron",
        hour=6,
        minute=30,
        id="daily_review_translation",
        name="每日翻译未翻译差评",
        replace_existing=True
    )

    scheduler.add_job(
        analyze_unanalyzed_reviews_job,
        trigger="cron",
        hour=7,
        minute=0,
        id="daily_review_analysis",
        name="每日AI分析未分析差评",
        replace_existing=True
    )

    scheduler.add_job(
        push_daily_review_notifications_job,
        trigger="cron",
        hour=8,
        minute=0,
        id="daily_review_notifications",
        name="每日推送未处理差评通知",
        replace_existing=True
    )

    # 选品评分：抓取是SQL直写数据库，后端无法被动感知，改为2小时轮询新记录（算分+高分自动AI分析）
    scheduler.add_job(
        recalc_product_selection_scores_job,
        trigger=IntervalTrigger(hours=2),
        id="product_selection_recalc_polling",
        name="选品评分轮询计算（新抓取自动算分+高分自动AI分析）",
        replace_existing=True
    )

    scheduler.add_job(
        check_overdue_purchase_orders_job,
        trigger="cron",
        hour=9,
        minute=0,
        id="overdue_purchase_check",
        name="检查超期未入库采购单",
        replace_existing=True
    )

    scheduler.add_job(
        cleanup_expired_ad_data_job,
        trigger="cron",
        hour=3,
        minute=0,
        id="ad_data_cleanup",
        name="广告数据清理任务",
        replace_existing=True
    )

    # 星拓 MCP 每日定时独立拉取（Q-3 经中间确认：与快照导入解耦，默认凌晨 2 点可配置）
    from config import get_settings
    _pull_hour = get_settings().STARTOP_PULL_HOUR
    scheduler.add_job(
        startop_pull_job,
        trigger="cron",
        hour=_pull_hour,
        minute=0,
        id="startop_word_pull",
        name="星拓搜索词拉取与自动打标",
        replace_existing=True
    )

    # 海外仓库存每日自动同步（默认 8:00，可配置）。与 FBA 同步共用同一套后台线程 + 内存状态机，
    # 因此定时触发与手动点击产生的是同一种任务，前端可实时看到同一份进度与结果。
    _overseas_hour = get_settings().OVERSEAS_SYNC_HOUR
    scheduler.add_job(
        overseas_inventory_sync_job,
        trigger="cron",
        hour=_overseas_hour,
        minute=0,
        id="overseas_inventory_sync",
        name="海外仓库存每日同步（飞书多维表）",
        replace_existing=True,
        misfire_grace_time=3600,   # 服务重启错过触发点时，1 小时内仍补跑一次
        coalesce=True              # 多次错过只补跑一次
    )

    # 海外仓快照补写：库存快照按天新建行（新行海外仓默认为 0），
    # 若当天导入发生在 8:00 同步之后，白天页面会显示全 0。
    # 每小时做一次廉价自查（快照日期未变时只有 2 条 COUNT 查询），必要时按明细表回写主行。
    scheduler.add_job(
        overseas_backfill_job,
        trigger=IntervalTrigger(minutes=60),
        id="overseas_inventory_backfill",
        name="海外仓库存快照补写（新快照自动回写主行）",
        replace_existing=True
    )

    scheduler.start()
    logger.info("定时任务调度器已启动")

    # 启动补跑：服务在 8:00 之后才启动（或当天重启过）时，最近一次同步若早于「最近一个 8:00」，
    # 延迟 60 秒补跑一次，避免用户上午打开页面看到的是昨天的海外仓数据。
    try:
        _schedule_overseas_catch_up()
    except Exception as e:
        logger.warning(f"海外仓启动补跑检查失败（不影响启动）: {e}")


def _schedule_overseas_catch_up():
    """启动时判断是否需要补跑当天的海外仓同步（只补一次，不影响定时任务本身）"""
    from datetime import datetime, timedelta
    from sqlalchemy import text
    from config import get_settings
    from database.database import SessionLocal

    now = datetime.now()
    boundary = now.replace(
        hour=get_settings().OVERSEAS_SYNC_HOUR, minute=0, second=0, microsecond=0
    )
    if now < boundary:
        # 今天 8:00 还没到 → 以昨天的 8:00 为基准
        boundary = boundary - timedelta(days=1)

    db = SessionLocal()
    try:
        last_synced = db.execute(
            text("SELECT MAX(updated_at) FROM overseas_inventories WHERE tenant_id = :tid"),
            {"tid": get_settings().OVERSEAS_SYNC_TENANT_ID},
        ).scalar()
    finally:
        db.close()

    if last_synced and last_synced >= boundary:
        return

    logger.info(
        f"检测到海外仓最近一次同步为 {last_synced or '从未同步'}，早于 {boundary}，60 秒后补跑一次"
    )
    scheduler.add_job(
        overseas_inventory_sync_job,
        trigger="date",
        run_date=datetime.now() + timedelta(seconds=60),
        id="overseas_inventory_sync_catchup",
        replace_existing=True,
    )


def startop_pull_job():
    """每日定时：星拓 MCP 拉取搜索词 → 自动打标（拉取完成后触发重算，与导入互不阻塞）"""
    from database.database import SessionLocal
    from services.startop_client import pull_words_to_db, StartopMCPError
    from services.tag_service import run_auto_tagging

    LOCK_KEY = "startop_word_pull"
    db = SessionLocal()
    try:
        if not acquire_distributed_lock(db, LOCK_KEY, timeout_minutes=60):
            db.close()
            return
        logger.info("========== 开始星拓搜索词拉取任务 ==========")
        try:
            result = pull_words_to_db(db)
            logger.info(f"星拓拉取完成: {result}")
        except StartopMCPError as e:
            # 拉取失败：流水已记录 FAILED_FALLBACK_READY，等待兜底报告导入；不抛出阻断
            logger.error(f"星拓拉取失败（兜底通道就绪标记已写入流水）: {e}")
            db.rollback()
            return
        # 拉取完成后触发自动打标重算（US-5 §5.5.4⑤）
        try:
            tag_result = run_auto_tagging(db, tenant_id=1)
            logger.info(f"自动打标完成: {tag_result}")
        except Exception as e:
            logger.error(f"自动打标失败: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"星拓拉取任务异常: {e}", exc_info=True)
    finally:
        release_distributed_lock(db, LOCK_KEY)
        db.close()


def overseas_inventory_sync_job():
    """每日定时：飞书海外仓多维表 → overseas_inventories + 库存快照主行

    与 FBA 同步完全一致的执行模型：
      1. 起一个后台 daemon 线程跑同步（start_sync_async），不占用调度线程 IO；
      2. 进度/结果写进程内状态机（get_sync_status），前端轮询同一个接口，
         因此定时任务与手动点击在页面上表现一致（都能实时看到进度与结果）；
      3. 页面切换不影响任务 —— 任务归属服务端进程，不归属 HTTP 请求/前端页面。

    这里额外持有分布式锁并在锁内等待任务结束，避免多 worker/多进程重复跑；
    正常一次约 5 秒，全量兜底扫描约 2 分钟，最长等待 20 分钟。
    """
    from database.database import SessionLocal
    from services.overseas_inventory_service import (
        get_sync_status,
        is_configured,
        start_sync_async,
    )

    LOCK_KEY = "overseas_inventory_sync"
    MAX_WAIT_SECONDS = 20 * 60
    db = SessionLocal()
    try:
        if not acquire_distributed_lock(db, LOCK_KEY, timeout_minutes=30):
            logger.info("其他进程正在同步海外仓，本进程跳过")
            return

        if not is_configured():
            logger.warning(
                "海外仓定时同步跳过：未配置 FEISHU_OVERSEAS_BASE_TOKEN / FEISHU_OVERSEAS_TABLE_ID"
            )
            return

        if get_sync_status().get("is_running"):
            logger.info("海外仓同步任务已在运行（手动触发中），本次定时任务跳过")
            return

        logger.info("========== 开始海外仓每日定时同步 ==========")
        from config import get_settings
        result = start_sync_async(tenant_id=get_settings().OVERSEAS_SYNC_TENANT_ID)
        if not result.get("started"):
            logger.warning(f"海外仓定时同步未启动: {result.get('message')}")
            return

        # 等待后台线程结束，保证锁在整个同步期间有效
        waited = 0
        last_step = ""
        while waited < MAX_WAIT_SECONDS:
            status = get_sync_status()
            step = status.get("step") or ""
            if step != last_step:
                logger.info(f"海外仓同步进度 {status.get('progress')}% - {step}")
                last_step = step
            if not status.get("is_running"):
                break
            time.sleep(2)
            waited += 2

        status = get_sync_status()
        if status.get("error"):
            logger.error(f"海外仓定时同步失败: {status.get('error')}")
        elif status.get("skipped"):
            logger.warning(f"海外仓定时同步被跳过: {status.get('step')}")
        elif status.get("is_running"):
            logger.warning(f"海外仓定时同步超过 {MAX_WAIT_SECONDS}s 仍未结束，停止等待（任务仍在后台运行）")
        else:
            logger.info(
                "海外仓定时同步完成: 源数据日期=%s, SKU=%s, 主行写入=%s, 快照日期=%s",
                status.get("source_date"), status.get("updated"),
                status.get("matched"), status.get("snapshot_date"),
            )
    except Exception as e:
        logger.error(f"海外仓定时同步任务异常: {e}", exc_info=True)
    finally:
        release_distributed_lock(db, LOCK_KEY)
        db.close()


def overseas_backfill_job():
    """每小时自查：新一天的库存快照导入后，把海外仓值补写到该快照的主行

    与同步任务解耦：不抓飞书、只用库内 overseas_inventories 做一次 JOIN 回写，
    因此没有新快照时成本仅为 2 条 COUNT 查询，也不会与同步任务互相干扰。
    """
    from database.database import SessionLocal
    from services.overseas_inventory_service import (
        ensure_latest_snapshot_backfilled,
        get_sync_status,
        is_configured,
    )

    if not is_configured():
        return
    if get_sync_status().get("is_running"):
        # 同步任务正在写主行，跳过本次自查，避免无意义的重复回写
        return

    from config import get_settings

    db = SessionLocal()
    try:
        matched = ensure_latest_snapshot_backfilled(db, tenant_id=get_settings().OVERSEAS_SYNC_TENANT_ID)
        if matched is not None:
            logger.info(f"检测到新库存快照，海外仓已补写主行 {matched} 行")
    except Exception as e:
        logger.warning(f"海外仓快照补写失败（不影响主流程）: {e}")
    finally:
        db.close()


def check_inventory_job():
    from database.database import SessionLocal
    LOCK_KEY = "inventory_check"
    db = SessionLocal()
    try:
        if not acquire_distributed_lock(db, LOCK_KEY):
            db.close()
            return
        logger.info("执行库存检查任务...")
    finally:
        release_distributed_lock(db, LOCK_KEY)
        db.close()


def check_reviews_job():
    from database.database import SessionLocal
    LOCK_KEY = "reviews_check"
    db = SessionLocal()
    try:
        if not acquire_distributed_lock(db, LOCK_KEY):
            db.close()
            return
        logger.info("执行差评监控任务...")
    finally:
        release_distributed_lock(db, LOCK_KEY)
        db.close()


def send_daily_report_job():
    from database.database import SessionLocal
    LOCK_KEY = "daily_report"
    db = SessionLocal()
    try:
        if not acquire_distributed_lock(db, LOCK_KEY):
            db.close()
            return
        logger.info("发送每日运营报告...")
    finally:
        release_distributed_lock(db, LOCK_KEY)
        db.close()


def translate_untranslated_reviews_job():
    """每天早上6点30分：批量翻译所有未翻译的差评（含已有分析但无翻译的历史数据）"""
    from database.database import SessionLocal
    from sqlalchemy import text
    from services.translate_service import translate_review

    db = SessionLocal()
    try:
        db.execute(text("SET NAMES 'utf8mb4' COLLATE 'utf8mb4_unicode_ci'"))

        # 查询所有需要翻译的差评（translated_content 为空或等于原文）
        query = text("""
            SELECT id, tenant_id, title, content, translated_content
            FROM reviews
            WHERE (translated_content IS NULL OR translated_content = '') AND content IS NOT NULL
            LIMIT 200
        """)
        result = db.execute(query)
        untranslated = result.fetchall()

        if not untranslated:
            logger.info("没有需要翻译的差评")
            return

        logger.info(f"发现 {len(untranslated)} 条需要翻译的差评，开始翻译...")
        success_count = 0
        fail_count = 0

        for row in untranslated:
            review_id = row[0]
            title = row[2] or ""
            content = row[3] or ""
            try:
                translated_title, translated_content = translate_review(title, content)
                if translated_content:
                    db.execute(text("""
                        UPDATE reviews
                        SET translated_title = :tt, translated_content = :tc
                        WHERE id = :rid
                    """), {
                        "tt": translated_title,
                        "tc": translated_content,
                        "rid": review_id,
                    })
                    db.commit()
                    success_count += 1
                    logger.info(f"翻译成功 review_id={review_id}")
                else:
                    fail_count += 1
                    logger.warning(f"翻译返回空值 review_id={review_id}")
            except Exception as e:
                fail_count += 1
                db.rollback()
                logger.error(f"翻译失败 review_id={review_id}: {e}")

        logger.info(f"差评翻译任务完成：成功 {success_count} 条，失败 {fail_count} 条")
    except Exception as e:
        logger.error(f"每日差评翻译任务失败: {e}")
        db.rollback()
    finally:
        db.close()


def analyze_unanalyzed_reviews_job():
    """每天早上7点：检测未分析的差评并进行AI分析（并发处理）"""
    from database.database import SessionLocal
    from sqlalchemy import text
    import json
    from services.chat_service import DEPARTMENT_PROMPT_RULES, _normalize_departments

    LOCK_KEY = "daily_review_analysis"
    db = SessionLocal()
    try:
        if not acquire_distributed_lock(db, LOCK_KEY):
            db.close()
            return
        
        db.execute(text("SET NAMES 'utf8mb4' COLLATE 'utf8mb4_unicode_ci'"))

        # 查询所有未分析的差评（review_analyses中不存在的差评）
        query = text("""
            SELECT r.id, r.tenant_id, r.title, r.content, r.translated_content,
                   r.rating, r.asin
            FROM reviews r
            LEFT JOIN review_analyses ra ON r.id = ra.review_id
            WHERE r.rating <= 3 AND ra.id IS NULL
            LIMIT 50
        """)
        result = db.execute(query)
        unanalyzed = result.fetchall()

        if not unanalyzed:
            logger.info("没有未分析的差评")
            return

        logger.info(f"发现 {len(unanalyzed)} 条未分析的差评，开始并发AI分析...")

        from config import get_settings
        settings = get_settings()
        from openai import OpenAI

        if not settings.OPENAI_API_KEY:
            logger.warning("OpenAI API未配置，跳过AI分析")
            return

        client = OpenAI(api_key=settings.OPENAI_API_KEY, base_url=settings.OPENAI_API_BASE)

        def analyze_single_review(row):
            """分析单条差评（线程安全：每个线程使用独立db session）"""
            thread_db = SessionLocal()
            try:
                review_id = row[0]
                tenant_id = row[1]
                title = row[2] or ""
                content = row[3] or ""
                translated_content = row[4] or ""
                rating = row[5]
                asin = row[6] or ""

                if not translated_content:
                    try:
                        from services.translate_service import translate_review
                        logger.info(f"开始翻译 review_id={review_id}")
                        _, translated_content = translate_review(title, content)
                        if translated_content:
                            thread_db.execute(text("UPDATE reviews SET translated_content=:tc WHERE id=:rid"),
                                       {"tc": translated_content, "rid": review_id})
                            thread_db.commit()
                            logger.info(f"翻译成功并保存 review_id={review_id}")
                        else:
                            logger.warning(f"翻译返回空值 review_id={review_id}")
                    except Exception as te:
                        logger.error(f"翻译失败 review_id={review_id}: {te}")

                prompt = f"""分析差评并进行重要性分级：
商品: {asin}
评分: {rating}星
标题: {title or '无'}
内容: {content}
翻译: {translated_content or '无'}

重要性分级规则：
1. high（最高级）：货不对板、颜色不对、产品不是同一种、规格不符
2. medium（第二级）：质量不好、破损、少件、缺配件、损坏
3. low（第三级）：其他所有场景

""" + DEPARTMENT_PROMPT_RULES + """

输出JSON: {"sentiment":"负面","sentiment_score":3,"key_points":[],"topics":[],"suggestions":[{"department":"purchasing","text":"建议1"}],"summary":"","importance_level":"high|medium|low","departments":["operations","purchasing","warehouse","design"]}"""

                response = client.chat.completions.create(
                    model=settings.OPENAI_MODEL,
                    messages=[{"role": "system", "content": "你是专业差评分析师，只输出JSON。"}, {"role": "user", "content": prompt}],
                    temperature=0.3
                )

                rc = response.choices[0].message.content.strip()
                if rc.startswith("```"):
                    rc = rc.split("\n", 1)[-1]
                    if rc.endswith("```"):
                        rc = rc[:-3]
                    rc = rc.strip()

                try:
                    ar = json.loads(rc)
                except json.JSONDecodeError:
                    ar = {"sentiment": "negative", "sentiment_score": 3, "key_points": [], "topics": [], "suggestions": [], "summary": rc[:200]}

                # 保存AI分析结果（使用ON DUPLICATE KEY UPDATE避免并发重复插入）
                depts_str = _normalize_departments(ar.get("departments") or ar.get("department"))
                thread_db.execute(text("""
                    INSERT INTO review_analyses (tenant_id, review_id, model, sentiment, sentiment_score, key_points, topics, suggestions, summary, raw_response, department, departments)
                    VALUES (:tid, :rid, :model, :sentiment, :score, :kp, :topics, :sug, :sum, :raw, :dept, :depts)
                    ON DUPLICATE KEY UPDATE
                        sentiment = VALUES(sentiment),
                        sentiment_score = VALUES(sentiment_score),
                        key_points = VALUES(key_points),
                        topics = VALUES(topics),
                        suggestions = VALUES(suggestions),
                        summary = VALUES(summary),
                        raw_response = VALUES(raw_response),
                        department = VALUES(department),
                        departments = VALUES(departments)
                """), {
                    "tid": tenant_id, "rid": review_id, "model": settings.OPENAI_MODEL,
                    "sentiment": ar.get("sentiment", "negative"), "score": ar.get("sentiment_score", 3),
                    "kp": json.dumps(ar.get("key_points", [])), "topics": json.dumps(ar.get("topics", [])),
                    "sug": json.dumps(ar.get("suggestions", [])), "sum": ar.get("summary", ""), "raw": rc,
                    "dept": depts_str.split(",")[0] if depts_str else "", "depts": depts_str
                })
                # 更新重要性等级
                importance_level = ar.get("importance_level", "low")
                if importance_level not in ["high", "medium", "low"]:
                    importance_level = "low"

                col_check = thread_db.execute(text("SHOW COLUMNS FROM reviews LIKE 'importance_level'"))
                if col_check.fetchone():
                    thread_db.execute(text("""
                        UPDATE reviews SET importance_level = :level WHERE id = :rid
                    """), {"level": importance_level, "rid": review_id})

                thread_db.commit()
                logger.info(f"评论 {review_id} AI分析完成，重要性: {importance_level}")
                return review_id, True
            except Exception as e:
                logger.error(f"分析评论 {row[0]} 失败: {e}")
                thread_db.rollback()
                return row[0], False
            finally:
                thread_db.close()

        # 并发分析，5个线程同时处理
        from concurrent.futures import ThreadPoolExecutor, as_completed
        success_count = 0
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(analyze_single_review, row): row for row in unanalyzed}
            for future in as_completed(futures):
                _, success = future.result()
                if success:
                    success_count += 1

        logger.info(f"每日AI分析完成：成功 {success_count}/{len(unanalyzed)} 条")

    except Exception as e:
        logger.error(f"每日AI分析任务失败: {e}")
    finally:
        release_distributed_lock(db, LOCK_KEY)
        db.close()


def push_daily_review_notifications_job():
    """每天早上8点：推送未处理差评通知给对应店铺分组的所有人员"""
    from database.database import SessionLocal
    from sqlalchemy import text
    from datetime import datetime, date

    LOCK_KEY = "daily_review_notifications"
    
    db = SessionLocal()
    try:
        if not acquire_distributed_lock(db, LOCK_KEY):
            db.close()
            return
        
        logger.info("========== 开始推送每日差评通知 ==========")
        
        db.execute(text("SET NAMES 'utf8mb4' COLLATE 'utf8mb4_unicode_ci'"))
        logger.info("数据库连接成功")
        
        # 检查今天是否已经推送过通知
        today = date.today().isoformat()
        check_query = text("""
            SELECT COUNT(*) FROM notifications 
            WHERE type = 'warning' 
              AND title LIKE '%未处理差评提醒%'
              AND DATE(created_at) = :today
        """)
        result = db.execute(check_query, {"today": today}).scalar()
        if result > 0:
            logger.info(f"今天 ({today}) 已经推送过差评通知，跳过本次推送")
            logger.info("========== 推送结束 ==========")
            return
        logger.info(f"今天 ({today}) 尚未推送通知，开始处理...")

        # 检查各表是否存在
        has_group_table = False
        has_user_stores_table = False
        has_notifications_table = False
        has_importance_level = False

        try:
            check = db.execute(text("SHOW TABLES LIKE 'store_groups'"))
            has_group_table = check.fetchone() is not None
            logger.info(f"store_groups 表: {'存在' if has_group_table else '不存在'}")
        except Exception as e:
            logger.error(f"检查 store_groups 表失败: {e}")
        
        try:
            check = db.execute(text("SHOW TABLES LIKE 'user_stores'"))
            has_user_stores_table = check.fetchone() is not None
            logger.info(f"user_stores 表: {'存在' if has_user_stores_table else '不存在'}")
        except Exception as e:
            logger.error(f"检查 user_stores 表失败: {e}")
        
        try:
            check = db.execute(text("SHOW TABLES LIKE 'notifications'"))
            has_notifications_table = check.fetchone() is not None
            logger.info(f"notifications 表: {'存在' if has_notifications_table else '不存在'}")
        except Exception as e:
            logger.error(f"检查 notifications 表失败: {e}")
        
        try:
            check_col = db.execute(text("SHOW COLUMNS FROM reviews LIKE 'importance_level'"))
            has_importance_level = check_col.fetchone() is not None
            logger.info(f"importance_level 字段: {'存在' if has_importance_level else '不存在'}")
        except Exception as e:
            logger.error(f"检查 importance_level 字段失败: {e}")

        # 检查必须的表是否都存在
        if not has_group_table or not has_user_stores_table or not has_notifications_table:
            logger.warning("必要表不存在，跳过通知推送")
            logger.warning(f"需要的表: store_groups={has_group_table}, user_stores={has_user_stores_table}, notifications={has_notifications_table}")
            return

        # 查询未处理的差评（status为new, read, processing）
        # 按差评所属店铺的店铺分组归类，只对 high 和 medium 重要级别的差评发送通知
        if has_importance_level:
            logger.info("使用包含 importance_level 的查询")
            pending_query = text("""
                SELECT r.id, r.asin, r.title, r.rating, r.importance_level, r.status,
                       s.group_id, sg.name as group_name, r.tenant_id
                FROM reviews r
                LEFT JOIN stores s ON r.store_id = s.id
                LEFT JOIN store_groups sg ON s.group_id = sg.id AND sg.deleted_at IS NULL
                WHERE r.rating <= 3
                  AND r.status IN ('new', 'read', 'processing')
                  AND s.group_id IS NOT NULL
                  AND r.importance_level IN ('high', 'medium')
            """)
        else:
            logger.info("使用不包含 importance_level 的查询")
            pending_query = text("""
                SELECT r.id, r.asin, r.title, r.rating, r.status,
                       s.group_id, sg.name as group_name, r.tenant_id
                FROM reviews r
                LEFT JOIN stores s ON r.store_id = s.id
                LEFT JOIN store_groups sg ON s.group_id = sg.id AND sg.deleted_at IS NULL
                WHERE r.rating <= 3
                  AND r.status IN ('new', 'read', 'processing')
                  AND s.group_id IS NOT NULL
            """)
        
        logger.info("执行差评查询...")
        pending_reviews = db.execute(pending_query).fetchall()
        logger.info(f"查询到 {len(pending_reviews)} 条符合条件的差评")

        if not pending_reviews:
            logger.info("没有未处理的差评，跳过推送")
            logger.info("========== 推送结束 ==========")
            return

        # 输出前几条差评详情用于调试
        logger.info("差评详情:")
        for i, row in enumerate(pending_reviews[:5]):
            if has_importance_level:
                logger.info(f"  [{i+1}] ID={row[0]}, ASIN={row[1]}, 评分={row[3]}, 重要性={row[4]}, 状态={row[5]}, 分组ID={row[6]}, 分组名={row[7]}")
            else:
                logger.info(f"  [{i+1}] ID={row[0]}, ASIN={row[1]}, 评分={row[3]}, 状态={row[4]}, 分组ID={row[5]}, 分组名={row[6]}")
        if len(pending_reviews) > 5:
            logger.info(f"  ... 还有 {len(pending_reviews) - 5} 条")

        # 按店铺分组统计（租户+分组为唯一键）
        group_stats = {}
        for row in pending_reviews:
            if has_importance_level:
                group_id = row[6]
                group_name = row[7] or f"分组{group_id}"
                tenant_id = row[8]
            else:
                group_id = row[5]
                group_name = row[6] or f"分组{group_id}"
                tenant_id = row[7]

            key = (tenant_id, group_id)
            if key not in group_stats:
                group_stats[key] = {"tenant_id": tenant_id, "group_id": group_id, "name": group_name, "total": 0, "high": 0, "medium": 0, "low": 0, "review_ids": []}
            group_stats[key]["total"] += 1

            if has_importance_level:
                level = str(row[4]) if row[4] else "medium"
                if level == "high":
                    group_stats[key]["high"] += 1
                elif level == "medium":
                    group_stats[key]["medium"] += 1
                else:
                    group_stats[key]["low"] += 1
            else:
                group_stats[key]["medium"] += 1
            
            group_stats[key]["review_ids"].append(str(row[0]))

        logger.info(f"按店铺分组完成，共 {len(group_stats)} 个分组有未处理差评")
        for key, stats in group_stats.items():
            logger.info(f"  分组 {stats['name']} (ID={stats['group_id']}, 租户={stats['tenant_id']}): 总计={stats['total']}, 严重={stats['high']}, 中等={stats['medium']}, 轻微={stats['low']}")

        # 为每个店铺分组的成员推送通知
        notification_count = 0
        notification_rows = []
        for key, stats in group_stats.items():
            tenant_id = stats["tenant_id"]
            logger.info(f"处理店铺分组 {stats['name']} (ID={stats['group_id']}, 租户={tenant_id})...")
            
            # 分组成员 = 被分配了该分组下店铺的用户（user_stores -> stores）
            members = db.execute(text("""
                SELECT DISTINCT us.user_id
                FROM user_stores us
                INNER JOIN stores s ON us.store_id = s.id
                INNER JOIN users u ON u.id = us.user_id AND u.deleted_at IS NULL
                WHERE s.group_id = :gid AND us.tenant_id = :tid AND s.deleted_at IS NULL
            """), {"gid": stats["group_id"], "tid": tenant_id}).fetchall()
            logger.info(f"  找到 {len(members)} 个分组成员")

            if not members:
                logger.warning(f"  分组 {stats['name']} 没有成员，跳过")
                continue

            title = f"【{stats['name']}】未处理差评提醒"
            content = f"您所在的店铺分组「{stats['name']}」有 {stats['total']} 条未处理差评（严重: {stats['high']}，中等: {stats['medium']}，轻微: {stats['low']}），请及时处理。"
            logger.info(f"  通知标题: {title}")
            logger.info(f"  通知内容: {content}")

            for member in members:
                user_id = member[0]
                exists = db.execute(text("""
                    SELECT COUNT(*) FROM notifications
                    WHERE tenant_id = :tenant_id AND user_id = :uid AND title = :title
                      AND created_at >= DATE_SUB(NOW(), INTERVAL 5 MINUTE)
                """), {"tenant_id": stats["tenant_id"], "uid": user_id, "title": title}).scalar()
                if exists > 0:
                    logger.info(f"  用户 ID={user_id} 已在5分钟内收到相同通知，跳过")
                    continue
                logger.info(f"  准备推送给用户 ID={user_id}")
                notification_rows.append({
                    "tenant_id": stats["tenant_id"],
                    "uid": user_id,
                    "title": title,
                    "content": content
                })

        if notification_rows:
            try:
                db.execute(text("""
                    INSERT INTO notifications (tenant_id, user_id, type, title, content, link)
                    VALUES (:tenant_id, :uid, 'warning', :title, :content, '/review')
                """), notification_rows)
                notification_count = len(notification_rows)
                logger.info(f"✅ 批量写入 {notification_count} 条通知")
            except Exception as e:
                logger.error(f"批量写入通知失败: {e}")

        db.commit()
        logger.info(f"========== 推送完成！共推送 {notification_count} 条通知，覆盖 {len(group_stats)} 个店铺分组 ==========")

    except Exception as e:
        logger.error(f"每日通知推送任务失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        db.rollback()
    finally:
        release_distributed_lock(db, LOCK_KEY)
        db.close()


# ========== 选品评分公式（模块级，供定时任务与自动AI分析共用） ==========

def _sel_r2(x):
    return round(float(x), 2)

def _sel_calc_rating(rating, review_count):
    if rating is None:
        return 20.0
    r = round(rating, 1)
    # 只按星级评分（抓取数据评论数普遍<10条，不再按评论数分档）
    if r >= 4.8: base = 18.0
    elif r >= 4.5: base = 16.0
    elif r >= 4.2: base = 13.0
    elif r >= 4.0: base = 10.0
    else: base = 5.0
    # 评论极少(≤3条)时参考价值低，打9折
    if (review_count or 0) <= 3:
        base = round(base * 0.9, 1)
    return base

def _sel_calc_sales(s):
    s = s or 0
    if s == 0: return 0.0
    if 1 <= s <= 5: return 3.0
    if 6 <= s <= 10: return 6.0
    if 11 <= s <= 15: return 9.0
    if 16 <= s <= 20: return 12.0
    if 21 <= s <= 25: return 15.0
    if 26 <= s <= 30: return 18.0
    return 20.0

def _sel_calc_penalty(rs):
    # 阈值与新星级阶梯对齐
    if rs >= 16: return 1.00
    elif rs >= 13: return 0.95
    elif rs >= 10: return 0.85
    elif rs >= 5: return 0.70
    else: return 0.50

def _sel_calc_composite(pf, ts, ss):
    return round(pf * ts * 0.6 + ss * 5 * 0.4, 2)

def _sel_calc_traffic(traffic_trend_str):
    import math
    import statistics
    import ast
    import json as _json
    if not traffic_trend_str:
        return 0.0, ""
    try:
        month_volume = ast.literal_eval(traffic_trend_str)
    except Exception:
        return 0.0, ""
    if not isinstance(month_volume, dict) or not month_volume:
        return 0.0, ""

    values = [v for v in month_volume.values() if isinstance(v, (int, float))]
    n = len(values)
    if n == 0:
        return 0.0, ""

    result = {
        "趋势方向强度分": 0.0,
        "趋势一致性分": 0.0,
        "相对增长倍数分": 0.0,
        "月均增长率分": 0.0,
        "趋势连续性分": 0.0,
        "波动惩罚分": 0.0,
        "趋势总分": 0.0,
        "历史最低值": _sel_r2(min(values)),
        "最新月份值": _sel_r2(values[-1]),
        "增长倍数": 0.0,
        "月均增长率": 0.0,
        "波动系数CV": 0.0,
    }

    if n >= 6:
        last_avg = sum(values[-3:]) / 3
        prev_avg = sum(values[-6:-3]) / 3
        R = last_avg / prev_avg if prev_avg > 0 else 0
        result["趋势方向强度分"] = _sel_r2(max(0, min(25, (R - 1) * 18)))

    if n >= 6:
        up = sum(1 for i in range(1, 6) if values[-6 + i] > values[-7 + i])
        result["趋势一致性分"] = _sel_r2((up / 5) * 10)

    if n >= 2 and min(values) > 0:
        G = values[-1] / min(values)
        result["增长倍数"] = _sel_r2(G)
        result["相对增长倍数分"] = _sel_r2(max(0, min(20, math.log2(G) * 6)))

    if n >= 4 and values[-4] > 0:
        M = (values[-1] / values[-4]) ** (1 / 4) - 1
        result["月均增长率"] = _sel_r2(M)
        result["月均增长率分"] = _sel_r2(max(0, min(10, M * 120)))

    if n >= 2:
        cur = max_streak = 0
        for i in range(1, n):
            if values[i] > values[i - 1]:
                cur += 1
                max_streak = max(max_streak, cur)
            else:
                cur = 0
        mapping = {2: 3, 3: 6, 4: 9, 5: 12}
        result["趋势连续性分"] = 15 if max_streak >= 6 else mapping.get(max_streak, 0)

    if n >= 6:
        last_6 = values[-6:]
        mean = statistics.mean(last_6)
        std = statistics.pstdev(last_6)
        CV = std / mean if mean > 0 else 0
        result["波动系数CV"] = _sel_r2(CV)
        if CV <= 0.25: result["波动惩罚分"] = 10
        elif CV <= 0.35: result["波动惩罚分"] = 7
        elif CV <= 0.50: result["波动惩罚分"] = 4

    total = (
        result["趋势方向强度分"]
        + result["趋势一致性分"]
        + result["相对增长倍数分"]
        + result["月均增长率分"]
        + result["趋势连续性分"]
        + result["波动惩罚分"]
    )
    raw_total = max(0, total)
    result["趋势总分"] = _sel_r2(raw_total)

    # 放大到满分100
    max_possible = 90
    final_score = round((raw_total / max_possible) * 100, 2) if max_possible > 0 else 0.0

    return final_score, _json.dumps(result, ensure_ascii=False)


def _auto_analyze_selection_task(selection_id: int, tenant_id: int):
    """自动AI分析单条选品：查数据 → AI(侵权+季节性) → 算分写库（独立短连接，线程内运行）"""
    import asyncio
    from database.database import SessionLocal
    from services.ai_analysis_service import analyze_product_selection
    from sqlalchemy import text as _text

    db = SessionLocal()
    try:
        db.execute(_text("SET NAMES 'utf8mb4' COLLATE 'utf8mb4_unicode_ci'"))
        row = db.execute(_text("""
            SELECT id, product_title, url, asin, image_url, rating, review_count, keywords,
                   price, commission, first_leg_cost, last_mile_cost, weight_kg,
                   cost_at_15_profit, product_type, monthly_sales, traffic_trend
            FROM product_selections
            WHERE id = :id AND tenant_id = :tid AND deleted_at IS NULL
        """), {"id": selection_id, "tid": tenant_id}).fetchone()
        if not row:
            return
        product_data = {
            "product_title": row[1], "url": row[2] or "", "asin": row[3] or "",
            "image_url": row[4] or "", "rating": row[5], "review_count": row[6],
            "keywords": row[7] or "", "price": float(row[8]) if row[8] is not None else None,
            "commission": float(row[9]) if row[9] is not None else None,
            "first_leg_cost": float(row[10]) if row[10] is not None else None,
            "last_mile_cost": float(row[11]) if row[11] is not None else None,
            "weight_kg": float(row[12]) if row[12] is not None else None,
            "cost_at_15_profit": float(row[13]) if row[13] is not None else None,
            "product_type": row[14] or "", "monthly_sales": row[15],
            "traffic_trend": row[16] or "",
        }
        rating, review_count, monthly_sales = row[5], row[6], row[15]
    finally:
        db.close()

    ai_result = asyncio.run(analyze_product_selection(product_data))
    if not ai_result:
        logger.warning(f"选品 {selection_id} 自动AI分析失败")
        return

    rating_score = _sel_calc_rating(rating, review_count)
    sales_score = _sel_calc_sales(monthly_sales)
    penalty_factor = _sel_calc_penalty(rating_score)
    traffic_score, traffic_score_result = _sel_calc_traffic(product_data.get("traffic_trend"))
    composite_score = _sel_calc_composite(penalty_factor, traffic_score, sales_score)

    db = SessionLocal()
    try:
        db.execute(_text("SET NAMES 'utf8mb4' COLLATE 'utf8mb4_unicode_ci'"))
        db.execute(_text("""
            UPDATE product_selections SET
                seasonality = :seasonality,
                infringement_analysis = :infringement_analysis,
                infringement_conclusion = :infringement_conclusion,
                traffic_score_result = :tsr, traffic_score = :ts,
                sales_score = :ss, rating_score = :rs,
                penalty_factor = :pf, composite_score = :cs,
                ai_raw_response = :raw, updated_at = NOW()
            WHERE id = :id
        """), {
            "id": selection_id,
            "seasonality": ai_result.get("seasonality", ""),
            "infringement_analysis": ai_result.get("infringement_analysis", ""),
            "infringement_conclusion": ai_result.get("infringement_conclusion", ""),
            "tsr": traffic_score_result, "ts": traffic_score,
            "ss": sales_score, "rs": rating_score,
            "pf": penalty_factor, "cs": composite_score,
            "raw": __import__("json").dumps(ai_result, ensure_ascii=False),
        })
        db.commit()
        logger.info(f"选品 {selection_id} 自动AI分析完成")
    except Exception as e:
        db.rollback()
        logger.error(f"选品 {selection_id} 自动AI分析写入失败: {e}")
    finally:
        db.close()


def recalc_product_selection_scores_job():
    """每天早上7点兜底：为未计算评分的选品记录补算（正常情况下新抓取入库时已实时计算），并对高分未分析的记录自动触发AI分析"""
    from database.database import SessionLocal
    from sqlalchemy import text
    import json

    LOCK_KEY = "daily_product_selection_recalc"
    db = SessionLocal()
    try:
        if not acquire_distributed_lock(db, LOCK_KEY):
            db.close()
            return

        logger.info("========== 开始每日选品数据评分计算 ==========")

        # 查找未计算过评分的记录（traffic_score IS NULL 或 traffic_score_result IS NULL）
        query = text("""
            SELECT id, rating, review_count, monthly_sales, traffic_trend
            FROM product_selections
            WHERE deleted_at IS NULL
              AND (traffic_score IS NULL OR traffic_score_result IS NULL)
        """)
        rows = db.execute(query).fetchall()

        if not rows:
            logger.info("没有需要计算评分的新选品数据")
        else:
            logger.info(f"发现 {len(rows)} 条待计算的选品记录")

        updated = 0
        for row in rows:
            rid = row[0]
            rating_score = _sel_calc_rating(row[1], row[2])
            sales_score = _sel_calc_sales(row[3])
            penalty_factor = _sel_calc_penalty(rating_score)
            traffic_score, traffic_result_json = _sel_calc_traffic(row[4])
            composite_score = _sel_calc_composite(penalty_factor, traffic_score, sales_score)

            db.execute(text("""
                UPDATE product_selections SET
                    traffic_score = :ts,
                    traffic_score_result = :tsr,
                    sales_score = :ss,
                    rating_score = :rs,
                    penalty_factor = :pf,
                    composite_score = :cs,
                    updated_at = NOW()
                WHERE id = :id
            """), {
                "id": rid,
                "ts": traffic_score,
                "tsr": traffic_result_json,
                "ss": sales_score,
                "rs": rating_score,
                "pf": penalty_factor,
                "cs": composite_score,
            })
            updated += 1

        db.commit()
        logger.info(f"========== 选品评分计算完成：共更新 {updated} 条记录 ==========")

        # 自动AI分析兜底：综合评分>=65 且未做过AI分析的记录（新抓取入库时已实时触发，此处处理历史/失败数据）
        pending_rows = db.execute(text("""
            SELECT id, tenant_id FROM product_selections
            WHERE deleted_at IS NULL
              AND composite_score IS NOT NULL AND composite_score >= 65
              AND (infringement_analysis IS NULL OR infringement_analysis = '')
        """)).fetchall()
        if pending_rows:
            logger.info(f"发现 {len(pending_rows)} 条高分选品待自动AI分析")
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=5) as pool:
                futures = [pool.submit(_auto_analyze_selection_task, r[0], r[1]) for r in pending_rows]
                for f in futures:
                    try:
                        f.result(timeout=900)
                    except Exception as e:
                        logger.error(f"选品自动AI分析任务异常: {e}")
            logger.info("========== 选品自动AI分析批次结束 ==========")
        else:
            logger.info("没有需要自动AI分析的高分选品记录")

    except Exception as e:
        logger.error(f"每日选品评分计算任务失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        db.rollback()
    finally:
        release_distributed_lock(db, LOCK_KEY)
        db.close()


def check_overdue_purchase_orders_job():
    """每天早上9点：检查已审批超14天仍未完成入库的采购单，通知相关人员"""
    from database.database import SessionLocal
    from sqlalchemy import text
    from datetime import datetime, date, timedelta

    logger.info("========== 开始检查超期未入库采购单 ==========")

    db = SessionLocal()
    try:
        today = date.today()
        cutoff_date = today - timedelta(days=14)

        # 查询已审批超过14天但状态未完成的采购单
        overdue_orders = db.execute(text("""
            SELECT po.id, po.order_number, po.tenant_id, po.status, po.approved_at,
                   po.created_by, po.approved_by, po.total_amount
            FROM purchase_orders po
            WHERE po.deleted_at IS NULL
              AND po.approved_at IS NOT NULL
              AND DATE(po.approved_at) <= :cutoff_date
              AND po.status NOT IN ('completed', 'cancelled')
            ORDER BY po.tenant_id, po.approved_at ASC
        """), {"cutoff_date": cutoff_date}).fetchall()

        if not overdue_orders:
            logger.info("没有超期未入库的采购单")
            logger.info("========== 检查结束 ==========")
            return

        logger.info(f"发现 {len(overdue_orders)} 个超期未入库的采购单")

        notification_rows = []
        for order in overdue_orders:
            order_id = order[0]
            order_number = order[1]
            tenant_id = order[2]
            status = order[3]
            approved_at = order[4]
            created_by = order[5]
            approved_by = order[6]
            total_amount = float(order[7]) if order[7] else 0

            # 计算已过天数
            days_passed = (today - approved_at.date()).days if approved_at else 0

            # 检查今天是否已发送过该订单的通知
            existing = db.execute(text("""
                SELECT COUNT(*) FROM notifications
                WHERE type = 'warning'
                  AND title LIKE :title_pattern
                  AND DATE(created_at) = :today
            """), {
                "title_pattern": f"%超期未入库提醒%{order_number}%",
                "today": today.isoformat()
            }).scalar()
            if existing > 0:
                logger.info(f"采购单 {order_number} 今天已通知过，跳过")
                continue

            # 查询已入库情况
            received_items = db.execute(text("""
                SELECT COALESCE(SUM(ioi.quantity), 0) as received_qty
                FROM inbound_order_items ioi
                JOIN inbound_orders io ON io.id = ioi.inbound_order_id AND io.deleted_at IS NULL
                WHERE io.purchase_order_id = :po_id
                  AND io.status = 'confirmed'
                  AND ioi.deleted_at IS NULL
            """), {"po_id": order_id}).fetchone()
            received_qty = int(received_items[0]) if received_items and received_items[0] else 0

            # 查询采购单总数量
            total_ordered = db.execute(text("""
                SELECT COALESCE(SUM(quantity), 0) as total_qty,
                       COALESCE(SUM(received_quantity), 0) as total_received
                FROM purchase_order_items
                WHERE purchase_order_id = :po_id AND deleted_at IS NULL
            """), {"po_id": order_id}).fetchone()
            total_qty = int(total_ordered[0]) if total_ordered else 0
            total_received = int(total_ordered[1]) if total_ordered and total_ordered[1] else 0

            # 获取产品明细
            items = db.execute(text("""
                SELECT poi.quantity, poi.received_quantity, p.name as product_name
                FROM purchase_order_items poi
                LEFT JOIN products p ON p.id = poi.product_id AND p.deleted_at IS NULL
                WHERE poi.purchase_order_id = :po_id AND poi.deleted_at IS NULL
            """), {"po_id": order_id}).fetchall()

            item_details = []
            not_received_items = []
            for item in items:
                ordered = int(item[0])
                received = int(item[1])
                product_name = item[2] or "未知产品"
                item_details.append(f"{product_name}(已订{ordered}/已收{received})")
                if received < ordered:
                    not_received_items.append(f"{product_name}(缺{ordered - received}件)")

            # 如果已入库数量 >= 采购数量，说明已完成入库，跳过
            if received_qty >= total_qty:
                logger.info(f"采购单 {order_number} 已全部入库({received_qty}/{total_qty})，跳过")
                continue

            # 构建通知内容
            status_label = {
                'approved': '已审批',
                'purchased': '已采购',
                'partial_received': '部分收货',
            }.get(status, status)

            title = f"超期未入库提醒 - 采购单 {order_number}"

            item_summary = "、".join(item_details[:3])
            if len(item_details) > 3:
                item_summary += f" 等{len(item_details)}项"

            missing_summary = "、".join(not_received_items[:3]) if not_received_items else "全部未入库"

            content = (
                f"采购单「{order_number}」已审批{days_passed}天(状态:{status_label})，"
                f"仍有商品未完成入库。\n"
                f"明细: {item_summary}\n"
                f"待入库: {missing_summary}\n"
                f"总金额: ¥{total_amount:.2f}"
            )

            notified_user_ids = set()
            if created_by:
                notified_user_ids.add(created_by)
            if approved_by:
                notified_user_ids.add(approved_by)

            for user_id in notified_user_ids:
                exists = db.execute(text("""
                    SELECT COUNT(*) FROM notifications
                    WHERE tenant_id = :tid AND user_id = :uid AND title = :title
                      AND created_at >= DATE_SUB(NOW(), INTERVAL 5 MINUTE)
                """), {"tid": tenant_id, "uid": user_id, "title": title}).scalar()
                if exists > 0:
                    logger.info(f"  用户 ID={user_id} 已在5分钟内收到相同通知，跳过")
                    continue
                notification_rows.append({
                    "tid": tenant_id,
                    "tenant_id": tenant_id,
                    "uid": user_id,
                    "title": title,
                    "content": content
                })

            logger.info(
                f"采购单 {order_number}: 已过{days_passed}天, "
                f"已入库{received_qty}/{total_qty}, "
                f"通知{len(notified_user_ids)}人"
            )

        if notification_rows:
            try:
                db.execute(text("""
                    INSERT INTO notifications (tenant_id, user_id, type, title, content, link)
                    VALUES (:tid, :uid, 'warning', :title, :content, '/purchase')
                """), notification_rows)
                logger.info(f"批量写入 {len(notification_rows)} 条超期采购单通知")
            except Exception as e:
                logger.error(f"批量写入通知失败: {e}")
                db.rollback()
                return

        db.commit()
        logger.info(f"========== 检查完成！共 {len(overdue_orders)} 个超期采购单，发送 {len(notification_rows)} 条通知 ==========")

    except Exception as e:
        logger.error(f"超期采购单检查任务失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        db.rollback()
    finally:
        db.close()


def cleanup_expired_ad_data_job():
    """每天凌晨3点：清理超过保留期的广告数据（7张表分批硬删除）

    - 通过 ad_retention_service.cleanup_expired_data 执行
    - 使用独立 db session，异常仅记录日志
    - 不传 tenant_id，清理所有租户的过期数据
    """
    from database.database import SessionLocal

    logger.info("========== 开始执行广告数据清理任务 ==========")
    db = SessionLocal()
    try:
        from services.ad_retention_service import cleanup_expired_data
        result = cleanup_expired_data(db, tenant_id=None)
        logger.info(
            f"广告数据清理任务完成 cutoff_date={result.get('cutoff_date')} "
            f"total_deleted={result.get('total_deleted', 0)} "
            f"success={result.get('success_count', 0)} "
            f"failed={result.get('failed_count', 0)}"
        )
        for tbl in result.get("tables", []):
            err_msg = tbl.get("error")
            logger.info(
                f"  表 {tbl.get('table')}: 删除 {tbl.get('deleted', 0)} 条 "
                + (f"错误: {err_msg}" if err_msg else "成功")
            )
    except Exception as e:
        logger.error(f"广告数据清理任务失败: {e}", exc_info=True)
        import traceback
        logger.error(traceback.format_exc())
    finally:
        db.close()
        logger.info("========== 广告数据清理任务执行结束 ==========")
