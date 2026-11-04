# -*- coding: utf-8 -*-
"""ai_image_tasks 增加 provider（local=本地 ComfyUI / relay=中转站 Nano Banana 2）。

幂等：列已存在则跳过。仅 DDL，存量行默认 local。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import text

from database.database import engine

TABLE = "ai_image_tasks"
COL = "provider"

with engine.begin() as conn:
    dbname = conn.execute(text("SELECT DATABASE()")).scalar()
    cols = [r[0] for r in conn.execute(text(f"SHOW COLUMNS FROM {TABLE}")).fetchall()]
    print(f"库: {dbname} | 表: {TABLE} | 现有列: {len(cols)}")
    if COL in cols:
        print(f"  {COL} 已存在，跳过")
    else:
        conn.execute(text(
            f"ALTER TABLE {TABLE} ADD COLUMN {COL} VARCHAR(20) NOT NULL DEFAULT 'local' "
            f"COMMENT '生成通道：local=本地Qwen Image 2.1 / relay=中转站' AFTER status"
        ))
        print(f"  已新增 {COL}")

    row = conn.execute(text(f"SHOW COLUMNS FROM {TABLE} LIKE '{COL}'")).fetchone()
    print("  校验:", row)
    dist = conn.execute(text(f"SELECT {COL}, COUNT(*) FROM {TABLE} GROUP BY {COL}")).fetchall()
    print("  分布:", dist)
