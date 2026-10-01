"""调试台（/devtools）挂载行为的回归测试。

为什么值得单独测：
"仅 dev 挂载"这条安全边界只靠 app/__init__.py 里一句 if 撑着，没有任何保护。
而本项目有过"判据被混用"的前科 —— DEBUG 曾经同时管着 CORS 通配、SQL echo 和
FastAPI debug 页，是评审时被揪出来的。判据一旦被人改成 DEBUG，这里要能变红。
"""

from app import create_app
from app.config import settings


def _route_paths(app) -> list[str]:
    return [str(getattr(route, "path", "")) for route in app.routes]


def test_devtools_mounted_in_dev(monkeypatch):
    """dev 环境应当挂载 /devtools。"""
    monkeypatch.setattr(settings, "APP_ENV", "dev")
    assert any("/devtools" in p for p in _route_paths(create_app()))


def test_devtools_not_mounted_in_prod(monkeypatch):
    """prod 环境绝不能挂载 —— 这是整条安全边界的核心断言。"""
    monkeypatch.setattr(settings, "APP_ENV", "prod")
    assert not any("/devtools" in p for p in _route_paths(create_app()))


def test_devtools_page_served_in_dev(client):
    """dev 环境下页面能正常打开。"""
    r = client.get("/devtools/")
    assert r.status_code == 200
    assert "接口调试台" in r.text


def test_devtools_unknown_file_uses_unified_envelope(client):
    """静态挂载下的 404 也要走统一响应体，不能吐裸 404。"""
    r = client.get("/devtools/not-exist.html")
    assert r.status_code == 200
    assert r.json()["code"] == 40400
