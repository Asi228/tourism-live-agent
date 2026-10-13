"""切分块接口 —— 只为**引用溯源**服务。

  GET /api/chunks/{chunk_id}     取一个块的完整正文 + 它所属文档的信息

**为什么单独开一个 `/chunks` 前缀**：答案里的 `[n]` 引用编号只带 chunk_id，
而"这个块属于哪个文档"要查过才知道。若挂在 `/documents/{id}/chunks/{cid}`
下面，前端就得先知道文档 id —— 那正是它要去查的东西，成了鸡生蛋。

**零迁移、零新依赖**：块的正文本来就存在 MySQL（`document_chunks.content`，
Milvus 里只有向量），溯源弹窗要的东西全都在，不需要记字符偏移量。
真要做到"在原文里滚动定位并高亮"才需要 offset，那是另一件事（且要动 schema）。

权限 = 块所属文档所属知识库的**读**权限，无权与不存在共用 404。
"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.response import ApiResponse, success
from app.db.session import get_db
from app.models.user import User
from app.schemas.document import ChunkDetailOut
from app.services import document_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chunks", tags=["引用溯源"])


@router.get(
    "/{chunk_id}",
    response_model=ApiResponse[ChunkDetailOut],
    summary="取一个引用块的完整原文（溯源用）",
)
def get_chunk(
    chunk_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """点答案里的 `[n]` 时调用，返回这块**完整**的正文。

    注意与问答接口里 `sources[].snippet` 的区别：snippet 是截断过的摘要，
    这里是原文全貌 —— 用户点引用就是想看"没被截掉的那部分"。

    没有权限（或不存在）一律 404：403 等于确认"这个 chunk_id 存在"。
    """
    chunk, doc = document_service.get_chunk_with_document(db, current_user, chunk_id)
    return success(
        ChunkDetailOut(
            id=chunk.id,
            document_id=doc.id,
            document_name=doc.filename,
            knowledge_base_id=doc.knowledge_base_id,
            chunk_index=chunk.chunk_index,
            page_number=chunk.page_number,
            char_count=chunk.char_count,
            content=chunk.content,
        )
    )
