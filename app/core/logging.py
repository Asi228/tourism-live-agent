import logging
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


def setup_logging(debug: bool = True) -> None:
    """统一日志格式：控制台 + 轮转文件。"""
    level = logging.DEBUG if debug else logging.INFO
    fmt = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    formatter = RedactingFormatter(fmt)

    # 中文 Windows 下 sys.stdout 默认是 cp936，写 UTF-8 日志会变乱码。
    # 这里强制 stdout 走 UTF-8（app.log 本来就指定了 utf-8，不受影响）。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

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
