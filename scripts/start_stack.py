"""一个终端启动 API、RPA Worker、定时调度器和 Vue 前端。"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _stop_processes(processes: list[tuple[str, subprocess.Popen]]) -> None:
    print("\n正在停止项目进程……", flush=True)
    for _, process in reversed(processes):
        if process.poll() is not None:
            continue
        try:
            if os.name == "nt":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(process.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and any(p.poll() is None for _, p in processes):
        time.sleep(0.2)
    for _, process in reversed(processes):
        if process.poll() is not None:
            continue
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def _wait_for_api(url: str, process: subprocess.Popen, timeout: int = 40) -> None:
    deadline = time.monotonic() + timeout
    with httpx.Client(trust_env=False, timeout=3) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"API 进程提前退出，exit={process.returncode}")
            try:
                response = client.get(f"{url.rstrip('/')}/api/health")
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.8)
    raise RuntimeError("API 启动超时，请查看上方日志")


def main() -> int:
    parser = argparse.ArgumentParser(description="一键启动直播电商 RPA Agent 开发栈")
    parser.add_argument("--skip-migrate", action="store_true", help="跳过 alembic upgrade head")
    parser.add_argument("--reload", action="store_true", help="为 API 开启代码热重载")
    parser.add_argument("--frontend-port", type=int, default=5173, help="Vue 开发服务器端口")
    args = parser.parse_args()

    if not (ROOT / ".env").exists():
        print("[FAIL] 缺少 .env，请先复制 .env.example 并填写配置。")
        return 1
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if npm is None:
        print("[FAIL] 没有找到 npm，请先安装 Node.js。")
        return 1
    if not (ROOT / "frontend" / "node_modules").exists():
        print("[FAIL] 前端依赖尚未安装，请先执行：cd frontend && npm ci")
        return 1

    python = sys.executable
    if not args.skip_migrate:
        print("[1/5] 升级数据库迁移……", flush=True)
        result = subprocess.run(
            [python, "-m", "alembic", "upgrade", "head"], cwd=ROOT, check=False
        )
        if result.returncode:
            print("[FAIL] 数据库迁移失败，未启动任何服务。")
            return result.returncode

    from app.config import settings

    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    popen_kwargs = {
        "cwd": ROOT,
        "creationflags": creationflags,
        "start_new_session": os.name != "nt",
    }
    api_command = [
        python, "-m", "uvicorn", "main:app", "--host", settings.APP_HOST,
        "--port", str(settings.APP_PORT),
    ]
    if args.reload:
        api_command.append("--reload")

    processes: list[tuple[str, subprocess.Popen]] = []
    try:
        print("[2/5] 启动 API……", flush=True)
        api = subprocess.Popen(api_command, **popen_kwargs)
        processes.append(("api", api))
        api_url = f"http://127.0.0.1:{settings.APP_PORT}"
        _wait_for_api(api_url, api)

        print("[3/5] 启动 RPA Worker……", flush=True)
        processes.append(("worker", subprocess.Popen(
            [python, "-m", "app.automation.worker"], **popen_kwargs
        )))
        print("[4/5] 启动定时调度器……", flush=True)
        processes.append(("scheduler", subprocess.Popen(
            [python, "-m", "app.automation.scheduler"], **popen_kwargs
        )))
        print("[5/5] 启动 Vue 前端……", flush=True)
        processes.append(("frontend", subprocess.Popen(
            [npm, "run", "dev", "--", "--port", str(args.frontend_port)], cwd=ROOT / "frontend",
            creationflags=creationflags, start_new_session=os.name != "nt",
        )))

        print(
            f"\n项目已启动：前端 http://127.0.0.1:{args.frontend_port} ｜ API {api_url}"
            "\n按 Ctrl+C 可统一停止全部进程。",
            flush=True,
        )
        while True:
            for name, process in processes:
                code = process.poll()
                if code is not None:
                    raise RuntimeError(f"{name} 进程退出，exit={code}")
            time.sleep(1)
    except KeyboardInterrupt:
        return 0
    except RuntimeError as exc:
        print(f"[FAIL] {exc}")
        return 1
    finally:
        _stop_processes(processes)


if __name__ == "__main__":
    raise SystemExit(main())
