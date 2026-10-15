"""文档与 chunk 的请求 / 响应模型。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DocumentOut(BaseModel):
    """文档信息。

    **刻意不包含 file_path 和 stored_name**：
    这两个是服务器内部路径，回给客户端等于把目录结构告诉外人，
    排查问题也用不上。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    knowledge_base_id: int
    uploader_id: int
    filename: str
    file_type: str
    file_size: int
    status: str
    chunk_count: int
    char_count: int
    error_msg: str | None = None
    created_at: datetime
    updated_at: datetime


class DocumentStatusOut(BaseModel):
    """前端轮询处理进度用，字段刻意少，轮询时传输量小。"""

    id: int
    status: str
    chunk_count: int
    error_msg: str | None = None


class ChunkDetailOut(BaseModel):
    """引用溯源用的一个块：完整正文 + 它出自哪儿。

    与 `ChunkOut` 的区别（**刻意分成两个**，不复用）：
      · `ChunkOut`      —— 切分结果列表用，不带文档信息（调用方已知文档）
      · `ChunkDetailOut` —— 溯源弹窗用，必须自带文档名/页码/知识库，
        否则前端拿到一个 chunk_id 还得再查一次文档
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_id: int
    document_name: str = Field(description="原始文件名（展示用）")
    knowledge_base_id: int
    chunk_index: int = Field(description="在文档内的序号，从 0 开始")
    page_number: int | None = None
    char_count: int
    content: str = Field(description="**完整**正文（不是问答里的 snippet 摘要）")


class ChunkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    chunk_index: int
    page_number: int | None = None
    char_count: int
    content: str


class UploadConfigOut(BaseModel):
    """上传约束（给前端取 `accept` 白名单用）。

    为什么要这个接口：白名单的权威是 `.env` 的 `ALLOWED_EXTENSIONS`，而前端曾经
    在 Documents.vue 里**又硬编码了一份** —— 改了 .env 之后，前端会继续放出一个
    服务端必拒的格式（2026-10-15 评审指出）。接口化之后，这份清单只剩一处权威。
    """

    allowed_extensions: list[str] = Field(
        description="允许上传的扩展名（小写、不带点），如 pdf / docx —— 前端用它拼 accept"
    )
