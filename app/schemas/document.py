"""文档与 chunk 的请求 / 响应模型。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


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


class ChunkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    chunk_index: int
    page_number: int | None = None
    char_count: int
    content: str
