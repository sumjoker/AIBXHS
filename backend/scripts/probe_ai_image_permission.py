"""权限探针：验证 ai_image:use 在生图接口上的拦截行为。

用真实用户铸 JWT，直打后端接口，只读（GET /api/ai-image/options、GET /api/ai-image/layouts），
不产生任何生图任务。断言矩阵：
  美工(meigong, 有 ai_image:use)      → 200
  运营(operator, 有 ai_image:use)     → 200
  运营(demo_yunying, 仅 ai_image)     → 200（无 ai_video:use，验证独立权限生效）
  采购(purchaser, 无)                 → 403 缺少权限: ai_image:use
  admin                              → 200（is_admin 直通）

在 backend 目录执行：python scripts/probe_ai_image_permission.py
"""
import os
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)
sys.stdout.reconfigure(encoding="utf-8")

import pkgutil  # noqa: E402
import importlib  # noqa: E402

import requests  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

import models as _models_pkg  # noqa: E402

for _m in pkgutil.iter_modules(_models_pkg.__path__):
    importlib.import_module(f"models.{_m.name}")

from config import get_settings  # noqa: E402
from services.auth_service import create_access_token  # noqa: E402

BASE = "http://127.0.0.1:8002"
ENDPOINTS = ["/api/ai-image/options", "/api/ai-image/layouts"]

# (说明, 用户名或 id, 期望状态码)
CASES = [
    ("美工 黄家淇(meigong)", 47, 200),
    ("运营 孙慧连(operator)", 16, 200),
    ("运营 demo_yunying(仅 ai_image)", 38, 200),
    ("采购 邓诗婷(purchaser)", 39, 403),
    ("admin k", 5, 200),
]


def main() -> None:
    eng = create_engine(get_settings().DATABASE_URL)
    sess = requests.Session()
    sess.trust_env = False  # 本地直连，不走系统代理

    failed = 0
    with eng.connect() as c:
        for label, uid, expect in CASES:
            u = c.execute(
                text("SELECT id, username, tenant_id FROM users WHERE id = :i"),
                {"i": uid},
            ).mappings().first()
            if not u:
                print(f"[SKIP] {label}: 用户 {uid} 不存在")
                continue
            token = create_access_token(
                data={"sub": u["username"], "uid": u["id"], "tid": u["tenant_id"]}
            )
            hdr = {"Authorization": f"Bearer {token}"}
            codes = []
            for ep in ENDPOINTS:
                r = sess.get(f"{BASE}{ep}", headers=hdr, timeout=15)
                codes.append(r.status_code)
            ok = all(x == expect for x in codes)
            if not ok:
                failed += 1
            detail = ""
            if expect == 403:
                r = sess.get(f"{BASE}{ENDPOINTS[0]}", headers=hdr, timeout=15)
                detail = f"  body={r.text[:90]}"
            print(f"{'[PASS]' if ok else '[FAIL]'} {label:32s} 期望 {expect} 实际 {codes}{detail}")

    print()
    print("全部通过" if failed == 0 else f"{failed} 项失败")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
