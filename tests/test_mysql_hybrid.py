"""MySQL FULLTEXT ngram 关键词路 —— **真连 MySQL** 的集成测试。

默认跳过。要跑：

    PowerShell:  $env:RUN_MYSQL_INTEGRATION="1"; python -m pytest tests/test_mysql_hybrid.py -v

为什么单开一个文件、默认不跑：它连**真实数据库**、会写测试数据再删。
放进主测试集会让"MySQL 没起"变成一堆红，别人 clone 下来第一件事就是测试全挂。

它盯的是 SQLite 测试库盯不到的东西：
  1. FULLTEXT ngram 索引真的建起来、`MATCH ... AGAINST` 真的能召回
     （迁移 d9e4f1a2b3c5 只对 MySQL 生效，SQLite 上是 no-op）
  2. 召回语义与 LIKE 分支一致：**任一词命中即召回**、按命中词数降序
  3. 口径没松：只认 completed、kb_ids 范围过滤照样生效

数据安全：全程只用高位 id 段（document 9000000xx / kb 900000003），
teardown 显式删干净 —— 不碰任何真实业务行（2026-10-14 出过"测试删真库数据"
的事故，这条纪律是拿事故换来的）。
"""

import os

import pytest
from sqlalchemy import create_engine, delete as sa_delete, select, text
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models.document import STATUS_COMPLETED, STATUS_PROCESSING, Document
from app.models.document_chunk import DocumentChunk
from app.repositories import document_chunk_repository

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_MYSQL_INTEGRATION") != "1",
    reason="需要真实 MySQL：设 RUN_MYSQL_INTEGRATION=1 才跑",
)

DOC_A = 900000001  # completed 文档
DOC_B = 900000002  # processing 文档（不该被召回）
KB_A = 900000003
KB_B = 900000004  # 另一个库（范围过滤用）
USER_ID = 900000005


@pytest.fixture()
def mysql_db():
    engine = create_engine(settings.mysql_url)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        # ---- 造数据 ----
        # 关掉 FK 检查：测试数据用高位 id 段（kb 9000000xx / user 9000000xx），
        # 不去真库的 knowledge_bases / users 里造配套行 —— 只碰自己造的行，
        # teardown 删干净，不残留、不依赖任何真实业务数据。
        db.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        docs = [
            Document(
                id=DOC_A,
                knowledge_base_id=KB_A,
                uploader_id=USER_ID,
                filename="年假制度.md",
                stored_name="ft-test-a.md",
                file_path="/ft-test/a.md",
                file_type="md",
                file_size=100,
                status=STATUS_COMPLETED,
                chunk_count=3,
                char_count=100,
            ),
            Document(
                id=DOC_B,
                knowledge_base_id=KB_A,
                uploader_id=USER_ID,
                filename="处理中的文档.md",
                stored_name="ft-test-b.md",
                file_path="/ft-test/b.md",
                file_type="md",
                file_size=100,
                status=STATUS_PROCESSING,
                chunk_count=1,
                char_count=100,
            ),
        ]
        db.add_all(docs)
        db.flush()
        chunks = [
            # 命中两个词：年假 + 几天
            DocumentChunk(document_id=DOC_A, chunk_index=0, page_number=None, char_count=20,
                          content="年假几天？入职满一年起可享受带薪年假。"),
            # 命中一个词：年假
            DocumentChunk(document_id=DOC_A, chunk_index=1, page_number=None, char_count=20,
                          content="年假按工龄分段计算，具体见薪酬制度。"),
            # 命中零个检索词（"报销"不在查询里）——不该被召回
            DocumentChunk(document_id=DOC_A, chunk_index=2, page_number=None, char_count=20,
                          content="差旅报销需在三十天内提交申请。"),
            # processing 文档的块：同样含"年假"，但不该被召回
            DocumentChunk(document_id=DOC_B, chunk_index=0, page_number=None, char_count=20,
                          content="年假几天（旧版答案，文档还在处理中）。"),
            # 🔴 共享 2-gram 但不含完整词的块：ngram 会把 ISO27001 切成
            # IS/SO/O2/27/70/01，这块含 IS/SO → NATURAL LANGUAGE MODE 会召回它，
            # 但 INSTR('ISO27001')=0（没有完整词）→ 修复后必须被 hit_count>0 过滤掉。
            # 2026-10-15 评审实锤的偏差场景（真库实测：该查询曾召回 9 行、8 行 hit=0）。
            DocumentChunk(document_id=DOC_A, chunk_index=3, page_number=None, char_count=20,
                          content="公司通过了 ISO 系列质量认证，体系持续有效。"),
        ]
        db.add_all(chunks)
        db.commit()
        yield db
    finally:
        # ---- 收尾：只删自己造的行 ----
        db.execute(sa_delete(DocumentChunk).where(DocumentChunk.document_id.in_([DOC_A, DOC_B])))
        db.execute(sa_delete(Document).where(Document.id.in_([DOC_A, DOC_B])))
        db.commit()
        db.execute(text("SET FOREIGN_KEY_CHECKS=1"))
        db.close()
        engine.dispose()


def test_fulltext_recall_matches_like_semantics(mysql_db):
    """召回 = 任一词命中；排序 = 命中词数 desc, id。两条都与 LIKE 分支逐字一致。"""
    # ⚠️ 范围限定 KB_A（测试库），不传 None：真库文档是流动的
    # （用户随时上传），"全库只有测试数据含检索词"这种假设会随数据变化假红
    # （2026-10-15 实测踩过：真库新增考勤文档后这条断言红了一次）。
    rows = document_chunk_repository.search_by_keywords(
        mysql_db, ["年假", "几天"], kb_ids=[KB_A], limit=10
    )
    contents = [c.content for c, _ in rows]
    # 🔴 先钉**条数**：completed 文档里含检索词的只有 2 块。少了这条断言，
    # "召回条件整个失效（退化成全表返回）"会被 limit=10 + id 排序掩盖 ——
    # 不含词的块 id 大、被截在 top10 外，in/not-in 断言偶然全过（变异验证抓到过）。
    assert len(rows) == 2, f"只该召回 2 块，实际 {len(rows)}: {contents}"
    assert "年假几天？入职满一年起可享受带薪年假。" in contents
    assert "年假按工龄分段计算，具体见薪酬制度。" in contents
    assert "差旅报销需在三十天内提交申请。" not in contents, "不含检索词的块不该召回"
    assert not any("处理中" in c for c in contents), "processing 文档的块不该召回"
    assert not any("ISO 系列" in c for c in contents), "2-gram 噪声块不该召回"
    # 命中两个词的排最前
    assert rows[0][1] == 2, rows[0][0].content
    assert rows[0][0].content.startswith("年假几天")


def test_keyword_k_and_scope_still_apply(mysql_db):
    """范围过滤与条数限制没被 FULLTEXT 分支丢掉。"""
    # 传另一个库 → 一条都不该有
    rows = document_chunk_repository.search_by_keywords(
        mysql_db, ["年假"], kb_ids=[KB_B], limit=10
    )
    assert rows == []
    # 传本库 + limit=1 → 只回一条
    rows = document_chunk_repository.search_by_keywords(
        mysql_db, ["年假"], kb_ids=[KB_A], limit=1
    )
    assert len(rows) == 1


def test_fulltext_filters_two_gram_noise(mysql_db):
    """🔴 共享 2-gram 但不含完整词的块，不能被召回（ngram 偏差守卫）。

    ngram_token_size=2 把查询切成二元组，NATURAL LANGUAGE MODE 任一片段命中
    即召回 —— 于是"ISO 系列"这种只共享 IS/SO 的块会带着 hit_count=0 混进候选，
    参与 RRF 后把向量路真命中挤出 candidate_k（2026-10-15 评审实锤 + 真库复现：
    查 ISO27001 曾召回 9 行、8 行 hit=0）。修复：MySQL 分支返回前过滤 hit_count>0，
    与 LIKE 分支语义逐字一致。这条用例就是那个过滤的守卫 —— 删掉过滤它必红。
    """
    rows = document_chunk_repository.search_by_keywords(
        mysql_db, ["ISO27001"], kb_ids=[KB_A], limit=50
    )
    contents = [c.content for c, _ in rows]
    assert not any("ISO 系列" in c for c in contents), (
        "2-gram 噪声块混进了候选 —— hit_count>0 过滤被删了？"
    )
    assert all(n > 0 for _, n in rows), "召回了 hit_count=0 的行"
    # 本库范围内没有任何块含 ISO27001 完整词 → 修复后应为空（全被过滤）
    assert rows == [], f"不含完整词时应召回空，实际 {contents}"
