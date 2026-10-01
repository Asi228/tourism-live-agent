"""文本清洗。

只做"确定不会改变语义"的处理，不做任何猜测性改写：
  ① 统一换行符（\\r\\n / \\r → \\n）
  ② 去掉 NUL 与零宽字符（PDF 抽取常见，肉眼看不见但会占 token、干扰检索）
  ③ 全角空格 → 半角空格
  ④ 去掉每行行尾空白
  ⑤ 连续 3 个以上换行压成 2 个（保留段落结构，去掉大片空白）

刻意不做的事：
- 不删"页码行"：像 "12" 这种孤立数字也可能是正文数据，误删代价高于收益
- 不做中文分词、不去停用词：这些属于检索阶段（Phase 7）的事，
  放进清洗会让"原文"和"入库文本"不一致，排查问题时对不上
"""

import re

# NUL + 常见零宽字符：零宽空格、零宽非连接符、零宽连接符、字节序标记
_INVISIBLE_RE = re.compile(r"[\x00\u200b\u200c\u200d\ufeff]")
_CRLF_RE = re.compile(r"\r\n?")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")


def clean_text(text: str) -> str:
    """清洗一段文本。幂等：清两次和清一次结果相同。"""
    if not text:
        return ""

    text = _CRLF_RE.sub("\n", text)
    text = _INVISIBLE_RE.sub("", text)
    text = text.replace("\u3000", " ")          # 全角空格
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = _MULTI_BLANK_RE.sub("\n\n", text)
    return text.strip()


def clean_pages(pages: list[tuple[int | None, str]]) -> list[tuple[int | None, str]]:
    """逐页清洗，丢掉清洗后为空的页。

    保留页边界而不是拼成一大段：页码是回答里"来源引用"的关键信息，
    拼起来之后就再也还原不出某段话出自第几页了。
    """
    cleaned: list[tuple[int | None, str]] = []
    for page_number, text in pages:
        result = clean_text(text)
        if result:
            cleaned.append((page_number, result))
    return cleaned
