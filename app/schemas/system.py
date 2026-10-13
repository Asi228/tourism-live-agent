"""系统状态相关的响应模型。

对应前端「系统状态」页的两块：**依赖连通性**（`/health`）与**向量库自检**（`/vector/health`）。
放同一个文件是因为它们在**同一个页面**上一起呈现 —— 排障时人问的是"这套系统还活着吗"，
不会去关心"这属于 health 模块还是 vector 模块"。

⚠️ 这两个接口此前都是**手拼 dict 且没有 `response_model`**。那种写法下，键名写错只会
静默少一个字段（前端显示空白，没有任何地方报错）。补上声明之后：

  · 字段名与类型成为**契约** —— 服务层拼错名字，这里当场就露；
  · 前端那些指标格依赖的字段名有了唯一出处。

⚠️ 反过来说：**没在这里声明的字段会被丢掉**（FastAPI 按 `response_model` 序列化）。
所以往这两个接口的返回值里加字段时，**必须同步加到这里**，否则前端永远拿不到。
"""

from pydantic import BaseModel


class HealthOut(BaseModel):
    """依赖连通性：每一项 `ok` / `fail`。

    三个名字直接写在 body 里，所以"到底哪一个挂了"一眼可见 ——
    这正是这个接口存在的意义（早先只探 MySQL 却号称探了三样，
    那是最坏的一种健康检查：它给出"健康"这个**错误答案**）。
    """

    api: str
    mysql: str
    milvus: str
    redis: str


class VectorHealthOut(BaseModel):
    """向量库自检。

    两种形态：`available=False` 时只有 `reason`（Milvus 不可用）；
    `True` 时带下面各项指标。所以除 `available` 外都是**可选** —— 但名字是契约，
    前端「向量库」卡片的指标格直接读它们。

    ⚠️ 刻意**不含 Milvus 地址**：那是内网拓扑信息，排查看服务端日志就够了。
    """

    available: bool

    # ---- available=False 时 ----
    reason: str | None = None

    # ---- available=True 时 ----
    server_version: str | None = None
    collection: str | None = None
    collection_exists: bool | None = None
    collections: list[str] | None = None
    vector_count: int | None = None
    kb_ids_in_vector_store: list[int] | None = None
    orphan_kb_ids: list[int] | None = None
    orphan_vector_count: int | None = None
    # 与孤儿向量**配对的反向问题**：文档处理好了，却一条向量都没有 → 永远检索不到
    documents_without_vectors: int | None = None
    documents_without_vectors_sample: list[dict] | None = None
