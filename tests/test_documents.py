"""文档模块测试：上传校验、后台处理、权限边界、切分与清洗。

测试不会碰真实 MySQL，也不会在项目目录留下上传文件：
  - 数据库用 conftest 的 SQLite 内存库（dependency_overrides）
  - 上传目录 monkeypatch 到 pytest 的 tmp_path
  - 后台任务用的 SessionLocal 也 monkeypatch 成测试 session 工厂
"""

import io
import uuid

import pytest
from pypdf import PdfWriter
from pydantic import ValidationError
from sqlalchemy import func, select

from app.config import CHUNK_SIZE_HARD_LIMIT, Settings, settings
from app.models import Document, DocumentChunk
from app.rag import cleaner, loader, splitter
from app.services import document_service


# ==================== 夹具与工具 ====================


@pytest.fixture
def doc_env(tmp_path, monkeypatch, session_factory):
    """把上传目录指到临时目录，并让后台处理任务写进测试库。"""
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    # process_document 是后台任务，会自己开 session；
    # 不换掉它就会连真实 MySQL，测试数据就漏进开发库了
    monkeypatch.setattr(document_service, "SessionLocal", session_factory)
    return tmp_path


def _upload(client, headers, kb_id, filename, content: bytes, content_type="text/plain"):
    return client.post(
        "/api/documents/upload",
        data={"kb_id": kb_id},
        files={"file": (filename, io.BytesIO(content), content_type)},
        headers=headers,
    )


def _make_kb(client, admin_headers, **extra) -> int:
    payload = {"name": "kb-" + uuid.uuid4().hex[:10], "description": "测试", **extra}
    r = client.post("/api/knowledge", json=payload, headers=admin_headers)
    assert r.json()["code"] == 0, r.json()
    return r.json()["data"]["id"]


def _txt(text: str) -> bytes:
    return text.encode("utf-8")


SAMPLE = "第一章 年假制度\n\n员工入职满一年可享受年假。\n\n申请流程：先提交申请单，由主管审批。\n"


# ==================== 上传：正常路径 ====================


def test_upload_txt_triggers_processing(client, make_user, doc_env):
    """上传后后台任务应当把文档处理成 completed，并落库 chunk。"""
    admin = make_user("admin")
    kb_id = _make_kb(client, admin["headers"])

    r = _upload(client, admin["headers"], kb_id, "handbook.txt", _txt(SAMPLE))
    body = r.json()
    assert body["code"] == 0, body
    doc_id = body["data"]["id"]
    assert body["data"]["filename"] == "handbook.txt"
    assert body["data"]["file_type"] == "txt"

    # TestClient 会把 BackgroundTasks 执行完再返回，所以此时状态已经落定
    detail = client.get(f"/api/documents/{doc_id}", headers=admin["headers"]).json()
    assert detail["code"] == 0
    assert detail["data"]["status"] == "completed", detail
    assert detail["data"]["chunk_count"] >= 1
    assert detail["data"]["char_count"] > 0
    assert detail["data"]["error_msg"] is None


def test_uploaded_files_land_in_kb_subdir_with_uuid_name(client, make_user, doc_env):
    """落盘文件必须放在 <上传目录>/<kb_id>/ 下，且文件名是 uuid（不含用户文件名）。"""
    admin = make_user("admin")
    kb_id = _make_kb(client, admin["headers"])

    r = _upload(client, admin["headers"], kb_id, "../../evil name.txt", _txt(SAMPLE))
    assert r.json()["code"] == 0, r.json()

    kb_dir = doc_env / str(kb_id)
    files = list(kb_dir.glob("*"))
    assert len(files) == 1
    stored = files[0]
    assert stored.name.endswith(".txt")
    assert "evil" not in stored.name and ".." not in stored.name
    # 原始文件名只出现在数据库里，展示时用
    assert r.json()["data"]["filename"] == "evil name.txt"


def test_chunks_carry_page_and_index_and_respect_size_limit(client, make_user, doc_env, monkeypatch):
    """chunk 序号连续、字符数不超过 CHUNK_SIZE。"""
    monkeypatch.setattr(settings, "CHUNK_SIZE", 40)
    monkeypatch.setattr(settings, "CHUNK_OVERLAP", 10)

    admin = make_user("admin")
    kb_id = _make_kb(client, admin["headers"])
    long_text = "年假制度说明。" * 40  # 280 字，必然切成多块

    r = _upload(client, admin["headers"], kb_id, "long.txt", _txt(long_text))
    doc_id = r.json()["data"]["id"]

    chunks = client.get(f"/api/documents/{doc_id}/chunks", headers=admin["headers"]).json()["data"]
    assert len(chunks) >= 2
    assert [c["chunk_index"] for c in chunks] == list(range(len(chunks)))
    assert all(c["char_count"] <= 40 for c in chunks)
    assert all(c["page_number"] is None for c in chunks)  # txt 没有页概念


def test_response_does_not_leak_server_path(client, make_user, doc_env):
    """响应里不能出现服务端路径（file_path / stored_name）。"""
    admin = make_user("admin")
    kb_id = _make_kb(client, admin["headers"])
    body = _upload(client, admin["headers"], kb_id, "a.txt", _txt(SAMPLE)).json()
    for leaked in ("file_path", "stored_name"):
        assert leaked not in body["data"]


# ==================== 上传：校验与失败路径 ====================


def test_upload_rejects_unsupported_extension(client, make_user, doc_env):
    admin = make_user("admin")
    kb_id = _make_kb(client, admin["headers"])
    r = _upload(client, admin["headers"], kb_id, "virus.exe", b"MZ\x90\x00")
    assert r.json()["code"] == 40000
    assert "不支持的文件类型" in r.json()["message"]


def test_upload_rejects_oversized_file(client, make_user, doc_env, monkeypatch):
    monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE_MB", 1)
    admin = make_user("admin")
    kb_id = _make_kb(client, admin["headers"])

    r = _upload(client, admin["headers"], kb_id, "big.txt", b"a" * (1024 * 1024 + 10))
    assert r.json()["code"] == 40000
    assert "上限" in r.json()["message"]
    # 半截文件必须被清掉，不能留在磁盘上
    assert list((doc_env / str(kb_id)).glob("*")) == []


def test_upload_rejects_empty_file(client, make_user, doc_env):
    admin = make_user("admin")
    kb_id = _make_kb(client, admin["headers"])
    r = _upload(client, admin["headers"], kb_id, "empty.txt", b"")
    assert r.json()["code"] == 40000
    assert "为空" in r.json()["message"]


def test_scanned_pdf_without_text_is_marked_failed(client, make_user, doc_env):
    """空白 PDF（无文本层）应当被识别为扫描件，文档状态置 failed 且给出原因。"""
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)

    admin = make_user("admin")
    kb_id = _make_kb(client, admin["headers"])
    r = _upload(client, admin["headers"], kb_id, "scan.pdf", buf.getvalue(),
                content_type="application/pdf")
    assert r.json()["code"] == 0, r.json()
    doc_id = r.json()["data"]["id"]

    detail = client.get(f"/api/documents/{doc_id}", headers=admin["headers"]).json()["data"]
    assert detail["status"] == "failed"
    assert "扫描件" in detail["error_msg"]
    assert detail["chunk_count"] == 0


def test_broken_pdf_is_marked_failed(client, make_user, doc_env):
    admin = make_user("admin")
    kb_id = _make_kb(client, admin["headers"])
    r = _upload(client, admin["headers"], kb_id, "broken.pdf", b"%PDF-1.4 not a real pdf",
                content_type="application/pdf")
    doc_id = r.json()["data"]["id"]
    detail = client.get(f"/api/documents/{doc_id}", headers=admin["headers"]).json()["data"]
    assert detail["status"] == "failed"
    assert detail["error_msg"]


# ==================== 权限边界 ====================


def test_upload_requires_write_permission(client, make_user, doc_env):
    """read 权限只读不能写 —— write 档位在 Phase 4 正式启用。"""
    admin = make_user("admin")
    employee = make_user("employee")
    kb_id = _make_kb(client, admin["headers"])

    r = client.post(
        f"/api/knowledge/{kb_id}/members",
        json={"user_id": employee["id"], "permission": "read"},
        headers=admin["headers"],
    )
    assert r.json()["code"] == 0

    resp = _upload(client, employee["headers"], kb_id, "a.txt", _txt(SAMPLE))
    assert resp.json()["code"] == 40300
    assert "权限不足" in resp.json()["message"]


def test_upload_with_write_permission_succeeds(client, make_user, doc_env):
    admin = make_user("admin")
    employee = make_user("employee")
    kb_id = _make_kb(client, admin["headers"])
    client.post(
        f"/api/knowledge/{kb_id}/members",
        json={"user_id": employee["id"], "permission": "write"},
        headers=admin["headers"],
    )

    r = _upload(client, employee["headers"], kb_id, "a.txt", _txt(SAMPLE))
    assert r.json()["code"] == 0, r.json()


def test_upload_to_inaccessible_kb_is_404_not_403(client, make_user, doc_env):
    """没被授权的库必须回"不存在或无权访问"，不能暴露"这个库存在"（防枚举）。"""
    admin = make_user("admin")
    employee = make_user("employee")
    kb_id = _make_kb(client, admin["headers"])

    r = _upload(client, employee["headers"], kb_id, "a.txt", _txt(SAMPLE))
    assert r.json()["code"] == 40400
    assert r.json()["message"] == "知识库不存在或无权访问"


def test_document_list_only_shows_accessible_kbs(client, make_user, doc_env):
    admin = make_user("admin")
    employee = make_user("employee")
    kb_id = _make_kb(client, admin["headers"])
    doc_id = _upload(client, admin["headers"], kb_id, "a.txt", _txt(SAMPLE)).json()["data"]["id"]

    # 员工不带 kb_id 列表 → 看不到别人的库里的文档
    r = client.get("/api/documents", headers=employee["headers"])
    assert r.json()["code"] == 0
    assert [d["id"] for d in r.json()["data"]] == []

    # 员工直接按 id 取 → 40400（不是 403，避免确认文档存在）
    r2 = client.get(f"/api/documents/{doc_id}", headers=employee["headers"])
    assert r2.json()["code"] == 40400

    # 管理员能看到
    r3 = client.get("/api/documents", headers=admin["headers"])
    assert doc_id in [d["id"] for d in r3.json()["data"]]

    # 授权 read 后员工能看到列表，但仍不能删（read < write）
    client.post(
        f"/api/knowledge/{kb_id}/members",
        json={"user_id": employee["id"], "permission": "read"},
        headers=admin["headers"],
    )
    assert doc_id in [d["id"] for d in client.get("/api/documents", headers=employee["headers"]).json()["data"]]
    assert client.delete(f"/api/documents/{doc_id}", headers=employee["headers"]).json()["code"] == 40400


def test_status_endpoint_is_minimal(client, make_user, doc_env):
    admin = make_user("admin")
    kb_id = _make_kb(client, admin["headers"])
    doc_id = _upload(client, admin["headers"], kb_id, "a.txt", _txt(SAMPLE)).json()["data"]["id"]

    r = client.get(f"/api/documents/{doc_id}/status", headers=admin["headers"])
    assert r.json()["code"] == 0
    assert set(r.json()["data"].keys()) == {"id", "status", "chunk_count", "error_msg"}


# ==================== 删除 ====================


def test_delete_document_removes_chunks_and_file(client, make_user, doc_env, session_factory):
    admin = make_user("admin")
    kb_id = _make_kb(client, admin["headers"])
    doc_id = _upload(client, admin["headers"], kb_id, "a.txt", _txt(SAMPLE)).json()["data"]["id"]

    files = list((doc_env / str(kb_id)).glob("*"))
    assert len(files) == 1

    r = client.delete(f"/api/documents/{doc_id}", headers=admin["headers"])
    assert r.json()["code"] == 0

    # 记录、chunk、磁盘文件三样都必须没了
    assert client.get(f"/api/documents/{doc_id}", headers=admin["headers"]).json()["code"] == 40400
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Document).where(Document.id == doc_id)) == 0
        assert db.scalar(
            select(func.count()).select_from(DocumentChunk).where(DocumentChunk.document_id == doc_id)
        ) == 0
    assert list((doc_env / str(kb_id)).glob("*")) == []


def test_deleting_kb_cascades_documents(client, make_user, doc_env, session_factory):
    admin = make_user("admin")
    kb_id = _make_kb(client, admin["headers"])
    doc_id = _upload(client, admin["headers"], kb_id, "a.txt", _txt(SAMPLE)).json()["data"]["id"]

    assert client.delete(f"/api/knowledge/{kb_id}", headers=admin["headers"]).json()["code"] == 0

    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Document).where(Document.id == doc_id)) == 0


def test_deleting_kb_removes_files_from_disk(client, make_user, doc_env):
    """删知识库必须连带清掉它的磁盘文件。

    只断言数据库行数为 0 是抓不到这个缺陷的：Document 行一删，file_path 就查不到了，
    文件会在 uploads/ 里永久变成无主垃圾 —— 已删除的库内容还留在服务器上。
    """
    admin = make_user("admin")
    kb_id = _make_kb(client, admin["headers"])
    kb_dir = doc_env / str(kb_id)

    for name in ("a.txt", "b.txt"):
        assert _upload(client, admin["headers"], kb_id, name, _txt(SAMPLE)).json()["code"] == 0
    assert len(list(kb_dir.glob("*"))) == 2, "上传后应先落两个文件"

    assert client.delete(f"/api/knowledge/{kb_id}", headers=admin["headers"]).json()["code"] == 0

    assert not kb_dir.exists(), f"删库后目录应被回收，残留: {list(kb_dir.glob('*'))}"


def test_upload_rejects_overlong_filename(client, make_user, doc_env):
    """文件名超过 documents.filename 列长（255）要回 40000，不能变成 500。

    真库开了 STRICT_TRANS_TABLES，超长会在 flush 时报 1406；
    而 SQLite 测试库对 varchar 长度根本不校验 —— 没有这条前置校验，
    用户输入问题会被报成"服务器内部错误"，而且测试永远是绿的。
    """
    admin = make_user("admin")
    kb_id = _make_kb(client, admin["headers"])

    r = _upload(client, admin["headers"], kb_id, "A" * 300 + ".txt", _txt(SAMPLE))
    assert r.json()["code"] == 40000, r.json()
    assert "文件名过长" in r.json()["message"]
    assert list((doc_env / str(kb_id)).glob("*")) == [], "校验没过就不该留下文件"


def test_upload_accepts_filename_at_the_limit(client, make_user, doc_env):
    """边界：正好 255 字符要放行，避免把校验写成 off-by-one。"""
    admin = make_user("admin")
    kb_id = _make_kb(client, admin["headers"])
    name = "A" * (255 - len(".txt")) + ".txt"
    assert len(name) == 255

    r = _upload(client, admin["headers"], kb_id, name, _txt(SAMPLE))
    assert r.json()["code"] == 0, r.json()
    assert r.json()["data"]["filename"] == name



# ==================== 单元测试：切分 / 清洗 / 解析 ====================


def test_split_respects_size_and_overlap():
    """每块不超上限，且相邻块之间存在重叠（段落大小接近真实文档的比例）。"""
    paragraph = "年假制度说明文字。" * 3  # 27 字一段
    text = "\n\n".join(paragraph for _ in range(6))
    chunks = splitter.split_text(text, chunk_size=60, chunk_overlap=20)

    assert len(chunks) >= 2
    assert all(len(c) <= 60 for c in chunks)
    # 相邻块必须真的有重叠：后一块开头能在前一块里找到
    for prev, cur in zip(chunks, chunks[1:]):
        assert cur[:10] in prev, f"缺少重叠:\n{prev!r}\n{cur!r}"


def test_split_overlap_shrinks_to_fit_instead_of_disappearing():
    """段落较长时，重叠要按剩余空间截断，而不是整段丢掉。"""
    text = "\n\n".join("甲乙丙丁戊己庚辛壬癸" for _ in range(8))  # 每段 10 字
    chunks = splitter.split_text(text, chunk_size=30, chunk_overlap=25)
    assert all(len(c) <= 30 for c in chunks)
    for prev, cur in zip(chunks, chunks[1:]):
        assert cur.split("\n")[0] in prev  # 首行来自上一块的尾部


def test_split_never_exceeds_size_on_long_single_paragraph():
    """没有标点的超长文本也不能切出超长块（走硬切兜底）。"""
    chunks = splitter.split_text("x" * 1000, chunk_size=100, chunk_overlap=10)
    assert all(len(c) <= 100 for c in chunks)
    assert len(chunks) == 10


def test_split_prefers_sentence_boundary():
    text = "第一句。" * 10
    chunks = splitter.split_text(text, chunk_size=20, chunk_overlap=0)
    assert all(len(c) <= 20 for c in chunks)
    assert all(c.endswith("。") for c in chunks)


def test_split_empty_and_invalid_args():
    assert splitter.split_text("   ", 100, 10) == []
    with pytest.raises(ValueError):
        splitter.split_text("abc", 0, 0)
    with pytest.raises(ValueError):
        # overlap >= size 会让切分不收敛，必须提前拦住
        splitter.split_text("abc" * 10, 50, 50)


def test_split_pages_keeps_page_numbers():
    pages = [(1, "第一页内容。" * 10), (2, "第二页内容。" * 10)]
    items = splitter.split_pages(pages, chunk_size=30, chunk_overlap=5)
    assert [i for i, _, _ in items] == list(range(len(items)))  # 全局序号连续
    assert {p for _, p, _ in items} == {1, 2}
    assert items[0][1] == 1


def test_cleaner_normalizes_and_is_idempotent():
    dirty = "标题\r\n\r\n\r\n\r\n正文\u3000有全角空格\u200b和零宽字符   \n"
    once = cleaner.clean_text(dirty)
    assert "\r" not in once
    assert "\u200b" not in once
    assert "\u3000" not in once
    assert "\n\n\n" not in once
    assert once == cleaner.clean_text(once)  # 幂等


def test_cleaner_drops_empty_pages():
    assert cleaner.clean_pages([(1, ""), (2, "   "), (3, "有内容")]) == [(3, "有内容")]


def test_loader_reads_gb18030_text(tmp_path):
    """中文 Windows 记事本默认编码是 GBK，必须能读，否则用户上传直接失败。"""
    path = tmp_path / "gbk.txt"
    path.write_bytes("公司年假制度".encode("gb18030"))
    pages = loader.load(path, "txt")
    assert pages == [(None, "公司年假制度")]


def test_loader_rejects_unknown_type(tmp_path):
    path = tmp_path / "a.csv"
    path.write_text("a,b", encoding="utf-8")
    with pytest.raises(Exception) as exc:
        loader.load(path, "csv")
    assert "不支持的文件类型" in str(exc.value)


# ==================== 配置校验：错在启动时就炸，别等处理文档时才失败 ====================


@pytest.mark.parametrize(
    "overrides",
    [
        {"CHUNK_SIZE": 0},
        {"CHUNK_SIZE": CHUNK_SIZE_HARD_LIMIT + 1},
        {"CHUNK_SIZE": 100, "CHUNK_OVERLAP": 100},
        {"CHUNK_SIZE": 100, "CHUNK_OVERLAP": -1},
        {"MAX_UPLOAD_SIZE_MB": 0},
        {"ALLOWED_EXTENSIONS": " , "},
    ],
)
def test_settings_reject_broken_document_config(overrides):
    """没有这些守卫，配错的后果是"每份文档都静默变成 failed"，极难定位。"""
    with pytest.raises(ValidationError):
        Settings(**overrides)


def test_valid_chunk_config_still_loads():
    s = Settings(CHUNK_SIZE=800, CHUNK_OVERLAP=120, MAX_UPLOAD_SIZE_MB=20)
    assert (s.CHUNK_SIZE, s.CHUNK_OVERLAP) == (800, 120)


def test_splitter_rejects_overlap_not_smaller_than_size():
    """splitter 自己的守卫也要留着：settings 只挡启动时那一次。"""
    with pytest.raises(ValueError):
        splitter.split_text("abc", chunk_size=10, chunk_overlap=10)

