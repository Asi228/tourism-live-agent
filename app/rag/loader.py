"""文档解析：把上传的文件变成 [(页码, 文本), ...]。

为什么要单独一个文件：
解析是"换一种文件格式就要改"的部分。把它隔离在这里，
上层（Service）只认"页码 + 文本"这个统一结构，
将来加 Excel / HTML 只需要在本文件里多一个分支。

返回结构用 (page_number, text)：
  PDF   → 页码从 1 开始，一页一项
  DOCX  → 整篇一个字符串（跟 TXT/MD 一样没有页的概念；docx 的分页由渲染决定，
          同一份文件在不同编辑器里页数都不一样，硬编页号是假数据）
  TXT/MD → page_number 为 None（这类文件没有页的概念，硬编成 1 是假数据）
"""

import logging
from pathlib import Path

from docx import Document as DocxDocument
from docx.oxml.ns import qn

from pypdf import PdfReader

from app.core.exceptions import AppException
from app.core.response import ErrorCode

logger = logging.getLogger(__name__)

SUPPORTED_TYPES = {"pdf", "txt", "md", "markdown", "docx"}

# 文本文件编码尝试顺序。gb18030 覆盖 GBK/GB2312，是中文 Windows 记事本的历史默认编码，
# 不试它的话，用户拿记事本存的 TXT 会直接失败。
TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "big5")

PageText = tuple[int | None, str]


def _load_pdf(path: Path) -> list[PageText]:
    try:
        reader = PdfReader(str(path))
    except Exception as e:  # noqa: BLE001 — pypdf 的异常类型不稳定，统一转成文件异常
        # ⚠️ **原文只进日志**：pypdf 的异常里常带着文件的绝对路径，
        # 而 message 会经 document_service 落库、再由接口回给客户端 ——
        # 拼进去等于把服务端目录结构送出去。
        logger.warning("PDF 打开失败 path=%s: %s", path, e)
        raise AppException(ErrorCode.FILE_ERROR, "PDF 打开失败，文件可能已损坏") from e

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


def _load_docx(path: Path) -> list[PageText]:
    """解析 .docx（Word）。

    **按文档流的原始顺序**取段落与表格 —— python-docx 的 `doc.paragraphs` /
    `doc.tables` 是分开列出的两个列表，直接用会把"表格夹在段落中间"的制度文档
    变成"所有段落 + 所有表格堆在末尾"，表格和它对应的上下文错位。
    所以遍历 body 的 XML 子节点，逐块判断是段落还是表格。

    表格转成 Markdown 行（`| a | b |`）：保留行列关系，模型读得懂；
    ⚠️ 合并单元格在 python-docx 里会把同一个格子的文本重复列出，这里不特殊处理
    —— 宁可多几段重复文字，也不要为它写一套脆弱的网格式重排。

    ⚠️ **已知边界（刻意不解析）**：页眉 / 页脚与文本框（textbox）里的文字 ——
    它们不在 body 的遍历里（页眉页脚挂在 section 上，文本框嵌在图形里），会被静默忽略。
    若制度文档把"版本号 / 生效日期"放页眉，这部分会缺失；README 已同步注明。
    """
    try:
        doc = DocxDocument(str(path))
    except Exception as e:  # noqa: BLE001 — python-docx 的异常类型不稳定，统一转文件异常
        # 同 PDF：原文（可能含绝对路径）只进日志，message 给人话
        logger.warning("DOCX 打开失败 path=%s: %s", path, e)
        raise AppException(ErrorCode.FILE_ERROR, "Word 文档打开失败，文件可能已损坏") from e

    from docx.table import Table
    from docx.text.paragraph import Paragraph

    lines: list[str] = []
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            text = (Paragraph(child, doc).text or "").strip()
            if text:
                lines.append(text)
        elif child.tag == qn("w:tbl"):
            for row in Table(child, doc).rows:
                cells = [(c.text or "").strip() for c in row.cells]
                if any(cells):
                    lines.append("| " + " | ".join(cells) + " |")
    content = "\n".join(lines)

    # 一个字都没抽到 → 空文档 / 全是图片（本项目暂不处理 Word 内的图片）
    if not content.strip():
        raise AppException(
            ErrorCode.FILE_ERROR,
            "该 Word 文档未解析出任何文本（可能是空文档，或内容全在图片里）",
        )
    # 整篇一个字符串：docx 没有可靠的"页"概念，page_number 用 None（同 TXT/MD）
    return [(None, content)]


def _load_text(path: Path) -> list[PageText]:
    for encoding in TEXT_ENCODINGS:
        try:
            return [(None, path.read_text(encoding=encoding))]
        except UnicodeDecodeError:
            continue
        except OSError as e:
            # 同上：`OSError` 里带绝对路径，原文进日志、message 给人话
            logger.warning("文本读取失败 path=%s: %s", path, e)
            raise AppException(ErrorCode.FILE_ERROR, "文件读取失败，请重新上传") from e
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
    if file_type == "docx":
        return _load_docx(path)
    return _load_text(path)
