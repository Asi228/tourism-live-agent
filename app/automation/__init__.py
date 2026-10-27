"""RPA 执行层：浏览器自动化采集与任务编排。

分层（为什么要拆）：
  · `page.py`     —— 页面抽象（协议 + 数据类）。采集编排只依赖协议，不依赖 Playwright，
                     单测用 FakePage 跑完整链路，不用真开浏览器。
  · `collector.py`—— 编排核心：登录 → 读表 → 逐 SKU 采集 → 写库存快照 → 触发巡检 → 步骤留痕。
                     与"用哪个浏览器"无关，逻辑可测。
  · `playwright_page.py` —— 真实实现（Playwright 同步 API，带截图）。
  · `worker.py`   —— 队列消费者：BLPOP Redis → 执行任务 → 状态机推进。

入口：`python -m app.automation.worker`（独立进程，与 FastAPI 服务分开跑）。

⚠️ **包名叫 automation 而不是 rpa，是踩坑后的决定**：项目里已有 `app/api/rpa.py`
（RPA 的 HTTP 接口）。如果这里也叫 `app/rpa/`，那么任何 `import app.rpa` 都会在
`app` 模块的命名空间里把全局名 `rpa` 覆盖成这个包 —— 于是 `app/__init__.py` 里
`include_router(rpa.router)` 会突然拿到包而不是 api 模块，报
`module 'app.rpa' has no attribute 'router'`。**而且报错只在"先导入了包"的进程里出现
（pytest / worker 启动时），单独跑服务却正常** —— 导入顺序决定的隐藏炸弹。
改名后两者互不干扰。
"""
