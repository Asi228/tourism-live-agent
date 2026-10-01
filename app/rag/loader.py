"""文档解析：把上传的文件变成 [(页码, 文本), ...]。

为什么要单独一个文件：
解析是"换一种文件格式就要改"的部分。把它隔离在这里，
上层（Service）只认"页码 + 文本"这个统一结构，
将来加 Word / HTML 只需要在本文件里多一个分支。

返回结构用 (page_number, text)：
  PDF   → 页码从 1 开始，一页一项
  TXT/MD → page_number 为 None（这类文件没有页的概念，硬编成 1 是假数据）
"""

import logging
from pathlib import Path

from pypdf import PdfReader

from app.core.exceptions import AppException
from app.core.response import ErrorCode

logger = logging.getLogger(__name__)

SUPPORTED_TYPES = {"pdf", "txt", "md", "markdown"}

# 文本文件编码尝试顺序。gb18030 覆盖 GBK/GB2312，是中文 Windows 记事本的历史默认编码，
# 不试它的话，用户拿记事本存的 TXT 会直接失败。
TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "big5")

PageText = tuple[int | None, str]


def _load_pdf(path: Path) -> list[PageText]:
    try:
        reader = PdfReader(str(path))
    except Exception as e:  # noqa: BLE001 — pypdf 的异常类型不稳定，统一转成文件异常
        raise AppException(ErrorCode.FILE_ERROR, f"PDF 解析失败：{e}") from e

    pages: list[PageText] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as e:  # noqa: BLE001 — 单页坏掉不该让整篇失败
            logger.warning("PDF 第 %s 页解析失败，已跳过: %s", index, e)
            text = ""
        pages.append((index, text))

    # 全部页面都没抽到文字 → 基本是扫描件（图片型 PDF），需要 OCR
    if not any(t.strip() for _, t in pages):
        raise AppException(
            ErrorCode.FILE_ERROR,
            "该 PDF 未解析出任何文本，可能是扫描件或图片型 PDF（本项目暂不支持 OCR）",
        )
    return pages


def _load_text(path: Path) -> list[PageText]:
    for encoding in TEXT_ENCODINGS:
        try:
            return [(None, path.read_text(encoding=encoding))]
        except UnicodeDecodeError:
            continue
        except OSError as e:
            raise AppException(ErrorCode.FILE_ERROR, f"文件读取失败：{e}") from e
    raise AppException(
        ErrorCode.FILE_ERROR,
        f"文本编码无法识别（已尝试 {' / '.join(TEXT_ENCODINGS)}）",
    )


def load(path: Path, file_type: str) -> list[PageText]:
    """按 file_type 解析文件。解析不出来一律抛 AppException(50001)。"""
    file_type = (file_type or "").lower().lstrip(".")
    if file_type not in SUPPORTED_TYPES:
        raise AppException(ErrorCode.FILE_ERROR, f"不支持的文件类型：{file_type}")

    if not path.exists():
        raise AppException(ErrorCode.FILE_ERROR, "文件已丢失，无法处理")

    if file_type == "pdf":
        return _load_pdf(path)
    return _load_text(path)
