"""开发期模拟电商后台；RPA 通过浏览器登录并抓取这里的页面。"""

import hashlib
import html
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Cookie, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db
from app.repositories import rpa_repository as repo

router = APIRouter(prefix="/mock-platform", tags=["模拟电商后台"])


def _token() -> str:
    raw = f"{settings.MOCK_PLATFORM_USERNAME}:{settings.MOCK_PLATFORM_PASSWORD}:{settings.JWT_SECRET}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _logged_in(mock_platform_session: str | None) -> bool:
    return mock_platform_session == _token()


def _page(body: str, title: str = "星播模拟电商后台") -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<style>
body{{margin:0;background:#f3f5f8;color:#1f2937;font:14px system-ui,"Microsoft YaHei",sans-serif}}
header{{padding:18px 28px;background:#111827;color:#fff}}header b{{font-size:18px}}header span{{margin-left:12px;color:#93c5fd}}
main{{max-width:1280px;margin:24px auto;padding:0 20px}}.card{{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:20px}}
table{{width:100%;border-collapse:collapse}}th,td{{padding:10px;border-bottom:1px solid #e5e7eb;text-align:left}}
th{{background:#f8fafc}}input,select{{box-sizing:border-box;width:100%;padding:7px;border:1px solid #cbd5e1;border-radius:5px}}
button{{padding:8px 14px;border:0;border-radius:5px;background:#2563eb;color:#fff;cursor:pointer}}.muted{{color:#64748b}}
.login{{max-width:380px;margin:80px auto}}.login label{{display:block;margin:14px 0 6px}}.toolbar{{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}}
</style></head><body><header><b>星播电商开放平台</b><span>商家运营后台 · 演示环境</span></header><main>{body}</main></body></html>""")


@router.get("/login", response_class=HTMLResponse)
def login_page(error: str | None = None):
    notice = '<p style="color:#dc2626">账号或密码错误</p>' if error else ""
    return _page(f"""<div class="card login"><h2>商家账号登录</h2><p class="muted">此页面模拟外部电商平台，供 Playwright RPA 演示使用。</p>{notice}
<form method="post" action="/mock-platform/login"><label>账号</label><input data-testid="username" name="username" required>
<label>密码</label><input data-testid="password" name="password" type="password" required>
<p><button data-testid="login-submit" type="submit">登录</button></p></form></div>""", "模拟平台登录")


@router.post("/login")
def login(username: str = Form(...), password: str = Form(...)):
    if username != settings.MOCK_PLATFORM_USERNAME or password != settings.MOCK_PLATFORM_PASSWORD:
        return RedirectResponse("/mock-platform/login?error=1", status_code=303)
    response = RedirectResponse("/mock-platform/products", status_code=303)
    response.set_cookie("mock_platform_session", _token(), httponly=True, samesite="lax")
    return response


def _dt(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%dT%H:%M") if value else ""


@router.get("/products", response_class=HTMLResponse)
def products(
    mock_platform_session: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
):
    if not _logged_in(mock_platform_session):
        return RedirectResponse("/mock-platform/login", status_code=303)
    rows = []
    for row in repo.list_mock_skus(db):
        attrs = {
            "sku-code": row.sku_code,
            "title": row.title,
            "status": row.platform_status,
            "list-price": str(row.list_price),
            "live-price": str(row.live_price) if row.live_price is not None else "",
            "available": str(row.available_quantity),
            "reserved": str(row.reserved_quantity),
            "promotion-starts": row.promotion_starts_at.isoformat() if row.promotion_starts_at else "",
            "promotion-ends": row.promotion_ends_at.isoformat() if row.promotion_ends_at else "",
            "stackable": "true" if row.stackable else "false",
        }
        data_attrs = " ".join(f'data-{key}="{html.escape(value, quote=True)}"' for key, value in attrs.items())
        rows.append(f"""<tr data-sku-row {data_attrs}><form method="post" action="/mock-platform/products/{row.id}">
<td><b>{html.escape(row.sku_code)}</b><input type="hidden" name="sku_code" value="{html.escape(row.sku_code)}"></td>
<td><input name="title" value="{html.escape(row.title, quote=True)}"></td>
<td><select name="platform_status"><option value="active" {'selected' if row.platform_status == 'active' else ''}>上架</option><option value="inactive" {'selected' if row.platform_status == 'inactive' else ''}>下架</option></select></td>
<td><input name="list_price" type="number" step="0.01" value="{row.list_price}"></td>
<td><input name="live_price" type="number" step="0.01" value="{row.live_price or ''}"></td>
<td><input name="available_quantity" type="number" value="{row.available_quantity}"></td>
<td><input name="reserved_quantity" type="number" value="{row.reserved_quantity}"></td>
<td><input name="promotion_starts_at" type="datetime-local" value="{_dt(row.promotion_starts_at)}"><input name="promotion_ends_at" type="datetime-local" value="{_dt(row.promotion_ends_at)}"></td>
<td><select name="stackable"><option value="false" {'selected' if not row.stackable else ''}>不可叠加</option><option value="true" {'selected' if row.stackable else ''}>允许叠加</option></select></td>
<td><button type="submit">保存</button></td></form></tr>""")
    table_rows = "".join(rows) or '<tr><td colspan="10" class="muted">暂无商品，请先在主系统初始化模拟平台数据。</td></tr>'
    return _page(f"""<div class="toolbar"><div><h2 style="margin:0">商品与库存</h2><span class="muted">页面数据由 RPA 浏览器采集</span></div><a href="/mock-platform/logout">退出</a></div>
<div class="card"><table data-testid="platform-products"><thead><tr><th>SKU</th><th>商品</th><th>状态</th><th>标价</th><th>直播价</th><th>可用</th><th>锁定</th><th>活动时间</th><th>叠加</th><th>操作</th></tr></thead><tbody>{table_rows}</tbody></table></div>""")


@router.post("/products/{row_id}")
def update_product(
    row_id: int,
    sku_code: str = Form(...),
    title: str = Form(...),
    platform_status: str = Form(...),
    list_price: Decimal = Form(...),
    live_price: str = Form(default=""),
    available_quantity: int = Form(...),
    reserved_quantity: int = Form(...),
    promotion_starts_at: str = Form(default=""),
    promotion_ends_at: str = Form(default=""),
    stackable: str = Form(default="false"),
    mock_platform_session: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
):
    if not _logged_in(mock_platform_session):
        return RedirectResponse("/mock-platform/login", status_code=303)
    row = repo.get_mock_sku(db, row_id)
    if row is not None:
        row.sku_code = sku_code.strip().upper()
        row.title = title.strip()
        row.platform_status = platform_status
        row.list_price = list_price
        row.live_price = Decimal(live_price) if live_price else None
        row.available_quantity = max(0, available_quantity)
        row.reserved_quantity = max(0, min(reserved_quantity, row.available_quantity))
        row.promotion_starts_at = datetime.fromisoformat(promotion_starts_at) if promotion_starts_at else None
        row.promotion_ends_at = datetime.fromisoformat(promotion_ends_at) if promotion_ends_at else None
        row.stackable = stackable == "true"
        db.commit()
    return RedirectResponse("/mock-platform/products", status_code=303)


@router.get("/logout")
def logout():
    response = RedirectResponse("/mock-platform/login", status_code=303)
    response.delete_cookie("mock_platform_session")
    return response
