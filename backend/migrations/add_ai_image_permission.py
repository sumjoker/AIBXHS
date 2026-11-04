"""迁移「AI生图」权限（ai_image:use）到所有已初始化权限的租户：
1. permissions 表插入该权限（缺失时）
2. 默认授予「美工」和「运营」类角色（用户 2026-10-30 指定）
3. admin 角色不需要显式授权（PermissionChecker 里有 is_admin 直通）

角色匹配按 code 白名单 + name 关键字双保险：
- 美工：code=meigong / name 含「美工」
- 运营：code in (operator, yunying, operator_zhuguan) / name 含「运营」
排除 admin（无需授权，且避免污染 role_permissions）。

幂等：权限已存在则复用；授权已存在（含软删）则恢复/跳过。

在 backend 目录执行：python migrations/add_ai_image_permission.py [--dry-run]
"""
import os
import sys

import pymysql

# 脚本目录是 migrations，sys.path[0] 不含 backend，必须显式补上（否则 import config 失败）
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from config import get_settings  # noqa: E402

PERM = {"name": "AI生图", "code": "ai_image:use", "type": "function",
        "module": "媒体创作中心", "sort_order": 73}

# 默认开启该权限的角色（用户指定：美工 + 运营）
DEFAULT_ROLE_CODES = {"meigong", "operator", "yunying", "operator_zhuguan"}
DEFAULT_ROLE_NAME_KW = ("美工", "运营")


def _match_default_role(code: str, name: str) -> bool:
    if code == "admin":
        return False
    if code in DEFAULT_ROLE_CODES:
        return True
    return any(kw in (name or "") for kw in DEFAULT_ROLE_NAME_KW)


def migrate(dry_run: bool = False):
    settings = get_settings()
    conn = pymysql.connect(
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        database=settings.DB_NAME,
        charset="utf8mb4",
    )

    added_perms = 0
    added_grants = 0
    restored_grants = 0
    granted_roles = []

    try:
        with conn.cursor() as cursor:
            # 1. 找出所有已初始化权限的租户
            cursor.execute("SELECT DISTINCT tenant_id FROM permissions WHERE deleted_at IS NULL")
            tenant_ids = [row[0] for row in cursor.fetchall()]
            print(f"[INFO] 已初始化权限的租户: {tenant_ids}")

            for tid in tenant_ids:
                # 2. 权限缺失时插入
                cursor.execute(
                    "SELECT id FROM permissions WHERE tenant_id = %s AND code = %s AND deleted_at IS NULL",
                    (tid, PERM["code"]),
                )
                row = cursor.fetchone()
                if row:
                    perm_id = row[0]
                    print(f"[SKIP] 租户 {tid} 已存在权限 {PERM['code']} (id={perm_id})")
                else:
                    if dry_run:
                        print(f"[DRY] 租户 {tid} 将新增权限 {PERM['code']}")
                        perm_id = None
                    else:
                        cursor.execute(
                            "INSERT INTO permissions (tenant_id, name, code, type, module, sort_order, created_at, updated_at) "
                            "VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())",
                            (tid, PERM["name"], PERM["code"], PERM["type"], PERM["module"], PERM["sort_order"]),
                        )
                        perm_id = cursor.lastrowid
                        added_perms += 1
                        print(f"[OK] 租户 {tid} 新增权限 {PERM['code']} (id={perm_id})")

                # 3. 默认授予美工 / 运营角色
                cursor.execute(
                    "SELECT id, name, code FROM roles WHERE tenant_id = %s AND deleted_at IS NULL",
                    (tid,),
                )
                roles = cursor.fetchall()
                for role_id, role_name, role_code in roles:
                    if not _match_default_role(role_code, role_name):
                        continue
                    if perm_id is None:
                        print(f"[DRY] 租户 {tid} 将授予角色 {role_name}({role_code})")
                        continue

                    # 含软删记录：存在则恢复，不存在则插入
                    cursor.execute(
                        "SELECT id, deleted_at FROM role_permissions "
                        "WHERE tenant_id = %s AND role_id = %s AND permission_id = %s",
                        (tid, role_id, perm_id),
                    )
                    rp = cursor.fetchone()
                    if rp and rp[1] is None:
                        print(f"[SKIP] 租户 {tid} 角色 {role_name}({role_code}) 已有权限")
                        continue
                    if dry_run:
                        print(f"[DRY] 租户 {tid} 将授予角色 {role_name}({role_code})")
                        continue
                    if rp:
                        cursor.execute(
                            "UPDATE role_permissions SET deleted_at = NULL, updated_at = NOW() WHERE id = %s",
                            (rp[0],),
                        )
                        restored_grants += 1
                        print(f"[OK] 租户 {tid} 角色 {role_name}({role_code}) 恢复授权")
                    else:
                        cursor.execute(
                            "INSERT INTO role_permissions (tenant_id, role_id, permission_id, created_at, updated_at) "
                            "VALUES (%s, %s, %s, NOW(), NOW())",
                            (tid, role_id, perm_id),
                        )
                        added_grants += 1
                        print(f"[OK] 租户 {tid} 角色 {role_name}({role_code}) 新增授权")
                    granted_roles.append(f"{tid}:{role_code}")

        if dry_run:
            conn.rollback()
            print("[DONE] dry-run 结束，未写入")
        else:
            conn.commit()
            print(f"[DONE] 迁移完成：新增权限 {added_perms} 个，新增授权 {added_grants} 条，"
                  f"恢复授权 {restored_grants} 条；授权角色 {granted_roles}")
    except Exception as e:
        conn.rollback()
        print(f"[ERR] 迁移过程出错：{e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    migrate(dry_run="--dry-run" in sys.argv)
