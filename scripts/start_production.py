"""生产容器入口：迁移、初始化角色/管理员、恢复演示数据、启动 API。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(*args: str) -> None:
    subprocess.run([sys.executable, *args], cwd=ROOT, check=True)


def main() -> int:
    print("[startup] 执行数据库迁移", flush=True)
    run("-m", "alembic", "upgrade", "head")

    print("[startup] 初始化角色与管理员", flush=True)
    from app.db.init_db import init_db

    init_db()

    if os.getenv("SEED_DEMO_ON_START", "true").lower() in {"1", "true", "yes", "on"}:
        print("[startup] 重建公开演示数据", flush=True)
        run("scripts/seed_tourism_demo.py", "--reset", "--remove-legacy")

    port = os.getenv("PORT", "8000")
    os.execv(
        sys.executable,
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", port],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
