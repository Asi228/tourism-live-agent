"""RPA 页面抽象 —— 采集编排与"用哪个浏览器"之间的唯一边界。

为什么要有这一层：采集任务里真正容易出错、也最需要测试的是**数据落库与状态推进**
（快照写没写、步骤有没有留痕、巡检有没有触发、平台缺 SKU 会不会整体崩）。
这些逻辑与 Playwright 无关。抽出协议后：
  · `collector` 只依赖本文件的协议 → 单测用 `FakePage` 全覆盖，不下载浏览器、不启服务；
  · 真实实现（playwright_page.py）只负责"把页面变成 PlatformSkuRow 列表 + 截图"。
"""

from dataclasses import dataclass, replace
from typing import Protocol


@dataclass(frozen=True)
class PlatformSkuRow:
    """平台页面上的一行商品数据（**采集结果**，不是本地业务数据）。

    字段与模拟平台页面的 `data-*` 属性一一对应（见 app/api/mock_platform.py）；
    数值保持字符串形态 —— 页面上的东西本来就是文本，解析与校验放到落库那一步，
    这样"页面读到了什么"与"能不能用"是两件事，出错时能说清是哪一步的问题。
    """

    sku_code: str
    title: str
    platform_status: str
    list_price: str
    live_price: str
    available_quantity: int
    reserved_quantity: int
    promotion_starts_at: str
    promotion_ends_at: str
    stackable: bool


class PlatformPage(Protocol):
    """RPA 执行器需要页面提供的最小能力集。"""

    def login(self, username: str, password: str) -> None:
        """登录平台。失败应抛异常（由 collector 转成"登录步骤失败"）。"""
        ...

    def read_products(self) -> list[PlatformSkuRow]:
        """读取商品列表页的全部行。空列表是合法结果（平台没有商品）。"""
        ...

    def screenshot(self, name: str) -> str:
        """截当前页面并存证，返回保存路径（相对仓库根，落库进步骤表）。"""
        ...

    # ---- 写操作（只有审批通过后才会被调用；见 collector.execute_repair_task）----
    def update_price(self, sku_code: str, live_price: str) -> None:
        """把某个 SKU 的直播价改成指定值（页面保存后应重新加载）。"""
        ...

    def set_status(self, sku_code: str, status: str) -> None:
        """把某个 SKU 的平台状态改成指定值（inactive = 下架）。"""
        ...


class FakePage:
    """测试替身：按脚本返回数据、按脚本抛异常、截图只记名字不落盘。

    刻意放在生产代码里而不是 tests/：它描述的是**协议的行为规范**
    （比如"登录失败要抛异常"），和协议放在一起，改协议时一眼能看到。
    """

    def __init__(
        self,
        rows: list[PlatformSkuRow] | None = None,
        *,
        login_error: Exception | None = None,
        read_error: Exception | None = None,
        ignore_writes: bool = False,
    ) -> None:
        # 用 dict 存：写操作要模拟页面刷新后读回新值，验证步骤依赖这一点
        self._rows = {r.sku_code: r for r in (rows or [])}
        self._login_error = login_error
        self._read_error = read_error
        # 模拟点了保存但平台没生效——用来测执行后二次验证失败
        self._ignore_writes = ignore_writes
        self.logged_in = False
        self.screenshots: list[str] = []
        self.updates: list[tuple[str, str, str]] = []

    def login(self, username: str, password: str) -> None:
        if self._login_error is not None:
            raise self._login_error
        self.logged_in = True

    def read_products(self) -> list[PlatformSkuRow]:
        if self._read_error is not None:
            raise self._read_error
        return list(self._rows.values())

    def update_price(self, sku_code: str, live_price: str) -> None:
        self.updates.append(("price", sku_code, live_price))
        if self._ignore_writes:
            return
        row = self._rows.get(sku_code)
        if row is None:
            raise RuntimeError(f"页面上找不到 SKU {sku_code}")
        self._rows[sku_code] = replace(row, live_price=live_price)

    def set_status(self, sku_code: str, status: str) -> None:
        self.updates.append(("status", sku_code, status))
        if self._ignore_writes:
            return
        row = self._rows.get(sku_code)
        if row is None:
            raise RuntimeError(f"页面上找不到 SKU {sku_code}")
        self._rows[sku_code] = replace(row, platform_status=status)

    def screenshot(self, name: str) -> str:
        path = f"fake-screenshots/{name}.png"
        self.screenshots.append(path)
        return path
