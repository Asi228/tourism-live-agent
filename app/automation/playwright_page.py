"""Playwright 真实实现 —— 只负责"把页面变成数据 + 截图"，不含任何业务判断。

工程要点：
  · 元素一律用 **data-testid / data-* 属性**定位，不依赖单元格顺序与文本内容 ——
    页面改样式、改列顺序都不会让 RPA 断掉（这也是模拟平台页面从一开始就埋 testid 的原因）。
  · 登录失败靠"等不到商品表"来判定，并给出可读的失败原因（而不是裸 TimeoutError）。
  · 截图目录按任务隔离（uploads/rpa_tasks/<task_id>/），路径写进步骤表当证据。
"""

from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from app.config import BASE_DIR, settings
from app.automation.page import PlatformSkuRow

_READ_ROWS_JS = """
els => els.map(el => ({
  sku_code: el.dataset.skuCode || "",
  title: el.dataset.title || "",
  platform_status: el.dataset.status || "",
  list_price: el.dataset.listPrice || "",
  live_price: el.dataset.livePrice || "",
  available_quantity: parseInt(el.dataset.available || "0", 10),
  reserved_quantity: parseInt(el.dataset.reserved || "0", 10),
  promotion_starts_at: el.dataset.promotionStarts || "",
  promotion_ends_at: el.dataset.promotionEnds || "",
  stackable: el.dataset.stackable === "true",
}))
"""


class PlaywrightPlatformPage:
    """同步 Playwright 实现（worker 是同步进程，不引 asyncio）。"""

    def __init__(self, base_url: str, screenshot_dir: Path) -> None:
        self._base_url = base_url.rstrip("/")
        self._screenshot_dir = Path(screenshot_dir)
        self._pw = None
        self._browser = None
        self._page = None

    # ---- 生命周期 ----
    def __enter__(self) -> "PlaywrightPlatformPage":
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=settings.RPA_HEADLESS)
        self._page = self._browser.new_page(
            viewport={"width": 1440, "height": 900},
            # 固定时区与语言，让页面上的时间展示稳定（截图与断言都更可复现）
            locale="zh-CN",
        )
        self._page.set_default_timeout(settings.RPA_TIMEOUT_MS)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            if self._pw is not None:
                self._pw.stop()
            self._browser = None
            self._page = None
            self._pw = None

    # ---- 协议实现 ----
    def login(self, username: str, password: str) -> None:
        assert self._page is not None, "PlaywrightPlatformPage 必须在 with 内使用"
        self._page.goto(f"{self._base_url}/login")
        self._page.fill('[data-testid="username"]', username)
        self._page.fill('[data-testid="password"]', password)
        self._page.click('[data-testid="login-submit"]')
        try:
            # 登录成功会 303 到 /products；失败会回到 /login?error=1 ——
            # 用"商品表出现"作为唯一成功信号，比判 URL 更稳（失败页 URL 也含 login）
            self._page.wait_for_selector('[data-testid="platform-products"]')
        except PlaywrightTimeoutError as exc:
            raise RuntimeError("登录模拟平台失败（账号密码错误或页面未加载）") from exc

    def read_products(self) -> list[PlatformSkuRow]:
        assert self._page is not None, "PlaywrightPlatformPage 必须在 with 内使用"
        raw = self._page.locator("tr[data-sku-row]").evaluate_all(_READ_ROWS_JS)
        return [PlatformSkuRow(**item) for item in raw]

    def _row(self, sku_code: str):
        """按 SKU 编码定位表格行（用 data-sku-code 属性，不依赖行序）。"""
        assert self._page is not None, "PlaywrightPlatformPage 必须在 with 内使用"
        loc = self._page.locator(f'tr[data-sku-row][data-sku-code="{sku_code}"]')
        if loc.count() == 0:
            raise RuntimeError(f"商品列表里找不到 SKU {sku_code}（可能已被删除）")
        return loc.first

    def _save_row(self, row) -> None:
        """点该行的保存按钮并等页面重载 —— 模拟平台是表单 POST + 303 跳转。"""
        assert self._page is not None
        with self._page.expect_navigation():
            row.locator('button[type="submit"]').click()
        self._page.wait_for_selector('[data-testid="platform-products"]')

    def update_price(self, sku_code: str, live_price: str) -> None:
        row = self._row(sku_code)
        row.locator('input[name="live_price"]').fill(live_price)
        self._save_row(row)

    def set_status(self, sku_code: str, status: str) -> None:
        row = self._row(sku_code)
        row.locator('select[name="platform_status"]').select_option(status)
        self._save_row(row)

    def screenshot(self, name: str) -> str:
        assert self._page is not None, "PlaywrightPlatformPage 必须在 with 内使用"
        stamp = datetime.now().strftime("%H%M%S")
        path = self._screenshot_dir / f"{name}-{stamp}.png"
        self._page.screenshot(path=str(path), full_page=True)
        # 存相对仓库根的路径：换机器/换盘符后仍能定位，也便于前端拼下载地址
        try:
            return path.relative_to(BASE_DIR).as_posix()
        except ValueError:
            return str(path)
