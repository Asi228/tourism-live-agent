import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# logs/ 位于项目根目录（app/core/logging.py 往上两级）
LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
LOG_DIR.mkdir(exist_ok=True)

MASK = "***"

# ==================== 日志脱敏 ====================
# 原则：只替换「敏感字段的值」，保留日志其余内容。
# 上一版直接把整条消息换成占位符，导致 "MySQL 连通性检查失败: Access denied ...
# (using password: YES)" 的真实原因被一起吞掉，排障无从下手。


# 1) key=value / key: value —— 只打码值
#    键名允许带前缀（MYSQL_PASSWORD、api_key 都能命中）；
#    用 \b 思路避免误伤：token_usage、secretary 这类不会被命中。
_KV_PATTERN = re.compile(
    r"(?i)([a-z0-9_\-]*(?:password|passwd|pwd|api[_-]?key|apikey|secret|token))"
    r"(\s*[=:]\s*)"
    r"(\"[^\"]*\"|'[^']*'|[^\s,;)\]}\"']+)"
)

# 2) Authorization 头：冒号后整段打码（形如 "Bearer eyJhbGci..."）
_AUTH_PATTERN = re.compile(r"(?i)\b(authorization)(\s*[=:]\s*)(\S.*)")

# 3) URL 里的凭据：scheme://user:password@host
_URL_CRED_PATTERN = re.compile(r"(?i)(://[^:@/\s]+:)([^@\s]+)(@)")


def redact(text: str) -> str:
    """对一段文本做脱敏。纯函数，可单独调用、可单测。"""
    text = _URL_CRED_PATTERN.sub(lambda m: f"{m.group(1)}{MASK}{m.group(3)}", text)
    text = _AUTH_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", text)
    text = _KV_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", text)
    return text


class RedactingFormatter(logging.Formatter):
    """在最终输出上脱敏（含 traceback）。

    为什么放在 Formatter 而不是 Filter：
    logger.exception() 的堆栈是 Formatter.formatException() 事后拼出来的，
    不经过 Filter —— 挂在 handler 上的 Filter 根本拦不到堆栈里的敏感信息。
    放在 Formatter 的 format() 出口，消息、参数、堆栈会被一起处理。
    """

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def _tune_console_encoding() -> None:
    """让控制台/管道的输出编码与「读它的人」对齐，避免中文乱码。

    实测踩过的坑：上一版对「非 tty」的 stdout 硬改成 UTF-8，结果在代码页 936 的
    终端里中文全变成「鍏佽潈鏉ユ簮」这种 —— 字节是写对了，可解码方按 GBK 读，照样错位。
    反过来，PyCharm 的 Run 窗口 / CI 日志收集器是按 UTF-8 读的，那时又确实需要 UTF-8。
    Python 无法知道对面是谁，所以这里不再替它做主：

      · 默认          → 什么都不改，跟随 Python 启动时的编码
                        （Windows 上 = 系统 ACP，中文环境通常是 cp936；设了
                        PYTHONIOENCODING 则按它走）
      · 需要 UTF-8 的场合（PyCharm / CI / 日志收集器）
                      → 设环境变量 LOG_CONSOLE_ENCODING=utf-8，或直接用标准的
                        PYTHONIOENCODING=utf-8
      · errors="replace" 只是兜底，防止个别生僻字抛 UnicodeEncodeError 把程序打断

    stderr 一起处理：未捕获异常的 traceback 走 stderr，同样会印中文。

    注意：写文件那一路日志（RotatingFileHandler）一直是显式 UTF-8，不受这里影响。
    """
    target = (os.getenv("LOG_CONSOLE_ENCODING") or "").strip()
    for stream in (sys.stdout, sys.stderr):
        try:
            if target:
                stream.reconfigure(encoding=target, errors="replace")
            else:
                stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            # 有些 IDE 会把 stdout 换成非 TextIOWrapper 对象，没有 reconfigure
            continue


def setup_logging(debug: bool = True) -> None:
    """统一日志格式：控制台 + 轮转文件。"""
    level = logging.DEBUG if debug else logging.INFO
    fmt = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    formatter = RedactingFormatter(fmt)

    _tune_console_encoding()

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        LOG_DIR / "app.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # 静音第三方库噪声
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
