"""文本切分（Chunk）。

策略：**按字符切，优先落在段落 / 句子边界，带重叠**。

为什么用字符而不是 token：
BGE-M3 走 API，本地不装 tokenizer（transformers + torch 几百 MB，本机扛不住）。
中文 1 个字符 ≤ 1 个 token，按字符数当上限用是偏保守的（切出来的块不会超长）。
要换成精确 token 计数，只改本文件即可，上层完全无感。

切分顺序：
  段落（空行分隔）→ 段落超长再按句子断 → 句子还超长就硬切
保证两件事：
  ① 每个 chunk 的字符数 ≤ chunk_size
  ② 相邻 chunk 之间有 chunk_overlap 个字符的重叠（避免答案正好被切断）
"""

import re

# 句子结束符。中英文都算上，按"这些字符出现在行内"来断句
_SENTENCE_END = set("。！？!?；;…")
# 段落分隔：一个或多个空行
_PARAGRAPH_RE = re.compile(r"\n\s*\n")


def _hard_split(text: str, max_len: int) -> list[str]:
    """把一段超长文本切成每块 ≤ max_len。

    先按句子结束符断，尽量凑到接近 max_len 再断；
    遇到一整句就超长（比如没有标点的长串），才退化成定长硬切。
    """
    if len(text) <= max_len:
        return [text]

    pieces: list[str] = []
    buf = ""
    for char in text:
        buf += char
        if len(buf) >= max_len:
            # 到上限了，必须断
            pieces.append(buf)
            buf = ""
        elif char in _SENTENCE_END and len(buf) >= max_len // 2:
            # 已经过半句，又正好遇到句号，是个好的断开点
            pieces.append(buf)
            buf = ""
    if buf:
        pieces.append(buf)
    return [p for p in pieces if p.strip()]


def _iter_segments(text: str, max_len: int):
    """产出每个长度 ≤ max_len 的切分单元（段落优先）。"""
    for paragraph in _PARAGRAPH_RE.split(text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        yield from _hard_split(paragraph, max_len)


def _with_overlap_tail(
    previous: str, segment: str, chunk_size: int, chunk_overlap: int
) -> str:
    """用上一块的尾部 + 当前段落，拼出新块的起始内容。

    重叠部分要**按剩余空间截断**：如果整段尾巴加不进去就全丢掉，
    那 overlap 在段落较长时几乎永远不会生效。这里改成"能塞多少塞多少"：
        room = chunk_size - len(segment) - 1（1 是连接用的换行）
    既保证结果不超 chunk_size，又让重叠尽可能发生。
    """
    if chunk_overlap <= 0:
        return segment
    room = chunk_size - len(segment) - 1
    if room <= 0:
        # 这一段自己就占满了 chunk_size，没有空间放重叠
        return segment
    tail = previous[-min(chunk_overlap, room):]
    return f"{tail}\n{segment}" if tail else segment


def split_text(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """把一段文本切成若干 chunk。

    chunk_overlap 是"上一块的尾部多少字符会被带到下一块开头"，
    这样跨块边界的句子在任一块里都能读到完整上下文。
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须大于 0")
    if chunk_overlap < 0:
        raise ValueError("chunk_overlap 不能为负数")
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap 必须小于 chunk_size，否则切分会不收敛")

    text = text.strip()
    if not text:
        return []

    chunks: list[str] = []
    current = ""

    for segment in _iter_segments(text, chunk_size):
        if not current:
            current = segment
            continue

        # 加得下就继续拼
        if len(current) + 1 + len(segment) <= chunk_size:
            current = f"{current}\n{segment}"
            continue

        # 加不下：收掉当前块，再带上一段尾巴作为下一块的开头
        chunks.append(current)
        current = _with_overlap_tail(current, segment, chunk_size, chunk_overlap)

    if current.strip():
        chunks.append(current)
    return chunks


def split_pages(
    pages: list[tuple[int | None, str]],
    chunk_size: int,
    chunk_overlap: int,
) -> list[tuple[int, int | None, str]]:
    """逐页切分，返回 [(全局序号, 页码, 内容), ...]。

    为什么逐页切、而不是把全文拼起来再切：
    拼起来就再也无法知道某个 chunk 出自第几页，而"来源引用显示第 N 页"
    是本项目的硬需求（需求文档 §17）。
    代价是重叠只发生在页内、不跨页 —— 这是可接受的取舍：
    页边界本身就是天然的内容边界。
    """
    result: list[tuple[int, int | None, str]] = []
    index = 0
    for page_number, text in pages:
        for chunk in split_text(text, chunk_size, chunk_overlap):
            result.append((index, page_number, chunk))
            index += 1
    return result
