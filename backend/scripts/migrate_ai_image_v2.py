# -*- coding: utf-8 -*-
"""AI 生图任务表 v2 迁移：A+/主副图分组参数 + 品牌自定义设置（幂等，DDL-only）。

用法：python scripts/migrate_ai_image_v2.py [--yes]
目标库以 config.get_settings().DATABASE_URL 为准（跑前先 SELECT DATABASE() 防呆）。
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")

from sqlalchemy import text

from config import get_settings

EXPECTED_DB = "bxhs_ai_assistance"  # 防呆：生产库名；demo 库为 bxhs_ai_assistance_demo

COLUMNS = {
    "aplus_type": "VARCHAR(30) NULL COMMENT 'A+ 类型（单选）：aplus_advanced/aplus_normal/aplus_mobile'",
    "main_types": "TEXT NULL COMMENT '主副图类型列表（JSON 数组）'",
    "main_aspect_ratio": "VARCHAR(20) NULL COMMENT '主副图生成比例'",
    "brand_settings": "TEXT NULL COMMENT '品牌自定义设置（JSON）'",
}


def main() -> int:
    engine = None
    try:
        from sqlalchemy import create_engine

        engine = create_engine(get_settings().DATABASE_URL, pool_pre_ping=True)
        with engine.connect() as conn:
            db = conn.execute(text("SELECT DATABASE()")).scalar()
            ver = conn.execute(text("SELECT VERSION()")).scalar()
            print(f"目标库 = {db}（MySQL {ver}）")
            ok = db == EXPECTED_DB or (db or "").endswith("_demo")
            if not ok:
                print(f"!! 目标库 {db!r} 不在预期内（{EXPECTED_DB} 或 *_demo），中止")
                return 1

            existing = {
                r[0] for r in conn.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema=DATABASE() AND table_name='ai_image_tasks'"
                )).all()
            }
            for col, ddl in COLUMNS.items():
                if col in existing:
                    print(f"[skip] {col} 已存在")
                    continue
                conn.execute(text(f"ALTER TABLE ai_image_tasks ADD COLUMN {col} {ddl}"))
                print(f"[ok] 新增列 {col}")
            conn.commit()
        print("迁移完成")
        return 0
    finally:
        if engine:
            engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
