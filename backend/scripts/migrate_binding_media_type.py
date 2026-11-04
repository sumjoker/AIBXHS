# -*- coding: utf-8 -*-
"""绑定申请表增加 media_type（video/image），使 AI 生图复用同一「绑定至产品」审核流程。

幂等：列已存在则跳过。仅 DDL，不改动已有数据（存量行默认 'video'）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import text

from database.database import engine

TABLE = "video_binding_requests"
COL = "media_type"

with engine.begin() as conn:
    dbname = conn.execute(text("SELECT DATABASE()")).scalar()
    cols = [r[0] for r in conn.execute(text(f"SHOW COLUMNS FROM {TABLE}")).fetchall()]
    print(f"库: {dbname} | 表: {TABLE} | 现有列: {len(cols)}")
    if COL in cols:
        print(f"  {COL} 已存在，跳过")
    else:
        conn.execute(text(
            f"ALTER TABLE {TABLE} ADD COLUMN {COL} VARCHAR(10) NOT NULL DEFAULT 'video' "
            f"COMMENT '媒体类型：video=视频 / image=图片' AFTER video_url"
        ))
        print(f"  已新增 {COL}")

    rows = conn.execute(text(f"SHOW COLUMNS FROM {TABLE} LIKE '{COL}'")).fetchall()
    print("  校验:", rows[0] if rows else "MISSING")
    total = conn.execute(text(f"SELECT COUNT(*) FROM {TABLE}")).scalar()
    dist = conn.execute(text(f"SELECT {COL}, COUNT(*) FROM {TABLE} GROUP BY {COL}")).fetchall()
    print(f"  行数 {total} | 分布 {dist}")
