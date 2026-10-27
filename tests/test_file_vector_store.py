from types import SimpleNamespace

from app.config import settings
from app.rag import file_vector_store


def _row(chunk_id, document_id, kb_id, vector):
    return SimpleNamespace(
        chunk_id=chunk_id,
        document_id=document_id,
        knowledge_base_id=kb_id,
        page_number=None,
        vector=vector,
    )


def test_file_backend_persists_and_ranks(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "FILE_VECTOR_PATH", str(tmp_path / "vectors.json"))
    file_vector_store.ensure_collection()
    file_vector_store.upsert(
        [_row(1, 10, 100, [1.0, 0.0]), _row(2, 20, 200, [0.0, 1.0])]
    )

    assert file_vector_store.count() == 2
    assert file_vector_store.list_kb_ids() == [100, 200]
    assert file_vector_store.search([0.9, 0.1], None, 1)[0]["chunk_id"] == 1
    assert file_vector_store.search([0.9, 0.1], [200], 1)[0]["chunk_id"] == 2


def test_file_backend_upsert_and_delete(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "FILE_VECTOR_PATH", str(tmp_path / "vectors.json"))
    file_vector_store.upsert([_row(1, 10, 100, [1.0, 0.0])])
    file_vector_store.upsert([_row(1, 10, 100, [0.0, 1.0])])

    assert file_vector_store.count() == 1
    assert file_vector_store.delete_by_document(10) == 1
    assert file_vector_store.count() == 0
