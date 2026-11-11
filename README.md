# 直播电商 RPA 智能运营 Agent

面向直播电商运营团队的自动化协同系统：围绕直播场次管理选品，保存商品、SKU、
库存和促销快照，并结合规则引擎、企业知识库与 RPA 完成直播前巡检、异常解释、
人工审批、自动修复和结果留痕。

**在线体验：** [项目介绍与演示入口](https://tourism-live-agent-portfolio.onrender.com/#/intro)

演示账号：`demo_operator / Demo@123456`。公开环境使用模拟业务数据并开启只读保护，
可以体验产品与场次、开播巡检、知识库问答、审批记录和自动化任务流程。

当前已经完成直播业务数据底座、运营知识库、确定性巡检、人工审批、Redis 任务队列、
Playwright RPA 执行与复检、定时监控、场次复盘和知识问答 Agent。开发环境使用仿真电商
后台复现外部平台页面，项目边界与后续规划见[项目方案](docs/直播电商RPA智能运营Agent项目方案.md)。

---

## 功能

| 模块 | 能力 |
|---|---|
| 直播任务 | 创建直播场次、维护本场选品、生成可追踪版本的开播准备包 |
| 商品与 SKU | 维护平台商品映射、SKU 规格和商品标价，作为 RPA 采集结果的本地业务镜像 |
| 库存与促销 | 保存库存快照、直播价、满减、优惠券及有效期，按规则计算最优到手价 |
| 运营资料 | 文档关联商品、SKU、资料类型、版本和有效期，支持后续资料完整性巡检 |
| 直播巡检 | 按场次检查选品、商品状态、库存、活动、计划到手价和资料有效性，输出准备分、阻断项、警告项及可开播结论 |
| 巡检规则中心 | 管理员可启停规则、调整阻断/警告/提示等级和库存时效参数；配置修改自动递增版本，巡检批次保存所用版本用于审计 |
| 审批与 RPA | 改价、下架等写操作遵循四眼审批，通过 Redis 入队并由 Playwright 执行；保存步骤、截图、前后值、验证结果和失败原因 |
| 监控与复盘 | 定时采集、重复告警聚合、任务成功率与耗时监控；按直播场次汇总选品、巡检、审批和 RPA 证据并导出 Markdown |
| 认证与权限 | JWT 登录注册；角色（admin / employee）；知识库级授权（read / write / admin 三档） |
| 知识库 | 创建、改名、公开/私有；成员授权与移除 |
| 文档 | 上传（PDF / Word / TXT / Markdown）→ 解析 → 清洗 → 切分 → 落库；后台状态流转（pending → processing → completed / failed）。Word 解析**按文档流顺序**取段落与表格（表格转 Markdown 行）；⚠️ 页眉/页脚与文本框不解析 |
| 向量化 | BGE-M3 批量向量化写入 Milvus；支持按文档或整库批量灌数据 |
| 检索 | **混合检索**：向量路（BGE-M3）+ 关键词路（jieba 提词 + MySQL 字面匹配）双路召回，**RRF 融合**后交 BGE-Reranker 精排；**权限过滤在检索阶段完成**（两路都过滤） |
| RAG 问答 | 检索 → 拼提示词（资料带 `[1][2]` 编号）→ 生成 → 返回答案 + 引用（文件名 / 页码 / 相似度 / 摘要） |
| 引用溯源 | 答案里的每个 `[n]` 可点开，弹窗显示该块引用的**完整原文** + 文档名 / 页码 / 块序号 / 字数（列表里的 snippet 是截断过的）。零迁移：正文本来就在 MySQL |
| Agent 问答 | 状态机多步决策（要不要查 → 查 / 取点名文档全文 / 列文档清单 → 够不够 → 换词再查 → 生成）；**三件工具**（语义检索 / 按名取全文 / 文档清单）；返回每步节点、耗时与决策 |
| 会话记忆 · 长期记忆 | **会话内**：问答落 `conversations` / `messages`，最近 N 轮带进提示词 —— 可以追问「那病假呢」，会话列表可翻回历史继续问（详情分页）。**跨会话**：`user_memories` + `/api/memories` 由用户**显式**写入，支持改内容与**单条开关**（关掉只是不拼进提示词，数据仍在）。**长对话**：超出窗口的旧轮次可**增量压缩成摘要**（两个默认关的开关见「已知限制」） |
| 常见问题置顶 | 每个库最多 10 条「问题 + 标准答案」；**命中即返回**，不检索也不调模型。见「FAQ 的口径」那节 |
| 答案反馈 | 每条回答可点「有用 / 没用」，没用时选原因（答案错误 / 与问题无关 / 文不对题 / 信息过时 / 其他）+ 备注。**重复提交是更新**，不是报"你已经评价过了" |
| 对话导出 | 一键把当前对话（含引用来源与"这一轮不完整"的原因）导成 Markdown。只导界面上看得到的，不会把脱敏内容写进文件 |
| 可观测性 | 执行轨迹落库（`agent_logs`）、token 统计、向量自检（孤儿向量、「已解析但未向量化」的文档清单、**检索可见性 `ok/missing/not_visible`**） |
| 成本与安全 | 按用户 + 接口类型的限流；提示词反幻觉规则；错误响应不泄露上游细节与内部地址 |

---

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | FastAPI + SQLAlchemy 2.x + Pydantic v2 + PyMySQL，Python 3.12 |
| 业务存储 | MySQL（用户、知识库、文档、**chunk 正文**、会话、审计） |
| 向量存储 | Milvus 2.6（只存向量与定位字段，**不存正文**） |
| 对话模型 | DeepSeek `deepseek-flash`（强制关闭思考模式，见下文） |
| 向量 / 重排 | BGE-M3（1024 维）、BGE-Reranker-v2-M3，均通过第三方兼容 API 调用 |
| 编排 | LangGraph（Agent 状态机）、LangChain（仅用于模型客户端封装） |
| 前端 | Vue3 + Vite + Element Plus + Pinia + Vue Router（`frontend/`）；另有单文件后端调试台 `devtools/` |

---

## 架构

分层：`api` → `services` → `repositories` → `db`，另有 `rag` / `agent` / `core` / `schemas` / `models`。

```
HTTP 请求
   │
   ├─ api/            路由 + 鉴权 / 限流依赖（不含业务逻辑）
   │
   ├─ services/       业务编排与**事务边界**
   │    ├─ rag_service        检索 → 提示词 → 生成 → 引用
   │    ├─ agent_service      Agent 编排 + 审计落库
   │    ├─ vector_service     ★检索期权限过滤 + 两段式检索
   │    ├─ document_service   解析 / 切分 / 状态流转（后台任务）
   │    └─ knowledge_service / auth_service / user_service
   │
   ├─ agent/          LangGraph 状态机
   │    analyze ─不需要检索─→ generate → END
   │       │
   │    需要检索
   │       ↓
   │    retrieve → evaluate ─够─→ generate → END
   │                  │
   │              不够且未到轮数上限
   │                  └──→ 回到 retrieve
   │
   ├─ rag/           提示词拼装与截断、LLM / 嵌入 / Rerank 客户端、Milvus 访问
   ├─ repositories/  数据访问（只 flush，不 commit —— 事务由 service 决定）
   └─ db/            Engine / Session / 建表与初始化
```

---

## 快速开始

**前置**：Python 3.12、MySQL、Milvus（standalone 即可）、一个对话模型的 API Key、
一个向量 / 重排模型的 API Key（可以是同一家服务商）。

```bash
python -m venv .venv
.venv/Scripts/activate                 # Windows；Linux/macOS 用 source .venv/bin/activate
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com

cp .env.example .env                   # 然后填写数据库、Milvus 与两个 API Key
alembic upgrade head                   # 建表（**必须**，详见下面第 8 节）
python main.py                         # 或 uvicorn main:app --reload
```

**建表由迁移负责**（`alembic upgrade head`）：空库能一路建到底，老库因为版本已 stamp 在基线上
不会被重复建。启动时只**校验**表齐不齐 —— 缺表会明确报错并提示先跑迁移，而不是偷偷
`create_all`（理由见第 8 节）。角色与 `.env` 里配置的初始管理员仍由启动流程幂等播种。

**启动前端**（另开一个终端）：

```bash
cd frontend
npm ci                                 # 按 lockfile 精确安装（版本已固定，可重现）
npm run dev                            # http://127.0.0.1:5173
```

> **装不上时看这里**：项目 `.npmrc` 把源指向了 `registry.npmmirror.com`（国内快）。
> 但镜像偶尔会缺某个新版本包（实测踩过一次：`@babel/parser@7.29.9` 在镜像上还没有），
> 此时会报 `ETARGET: No matching version found`。**加一个参数走官方源即可**：
>
> ```bash
> npm ci --registry=https://registry.npmjs.org
> ```
>
> （注意 `npm ci` 同样会受 `.npmrc` 影响 —— npm 的 `replace-registry-host` 默认会把
> lockfile 里的 registry 地址替换成当前配置的源，所以不能指望靠 lockfile 绕开镜像。）

前端把 `/api` 通过 Vite 代理转发到 `127.0.0.1:8000` —— **走的是同源调用，后端不需要配 CORS**，
而且和将来的生产部署（同域反代）形态一致。生产构建用 `npm run build`。

- 正式前端：`http://127.0.0.1:5173`（`vite.config.js` 里显式绑了 IPv4 的 `127.0.0.1`）
- 接口文档：`http://127.0.0.1:8000/docs`
- 后端调试台：`http://127.0.0.1:8000/devtools/`（单文件、直接打接口，排障用）
- 检索调试（管理员菜单「检索调试」）：只检索不调模型，看双路召回/融合/重排的原始结果

**测试**：

```bash
pytest -q                                          # 单元测试（不打任何真实外部接口）

# 集成测试默认全部跳过（它们依赖 VM 上的 Milvus / Redis、或本机 MySQL，放进主测试集
# 会因为"服务没开机"变成一片红，别人 clone 下来第一件事就是测试全挂）。三个开关分别打开：
RUN_MILVUS_INTEGRATION=1 pytest tests/test_vectors_integration.py -v    # 连真实 Milvus
RUN_REDIS_INTEGRATION=1  pytest tests/test_limiter_integration.py -v    # 连真实 Redis
RUN_MYSQL_INTEGRATION=1  pytest tests/test_migrations.py -v             # 迁移 drift 检查（连真实 MySQL）
RUN_MYSQL_INTEGRATION=1  pytest tests/test_mysql_hybrid.py -v           # 关键词路 FULLTEXT 真库行为

# 一起开（需要两台 VM 都在跑）
RUN_MILVUS_INTEGRATION=1 RUN_REDIS_INTEGRATION=1 RUN_MYSQL_INTEGRATION=1 pytest -v
```

直播运营 Agent 使用固定业务评测集验证结构化动作、审批边界、目标值、风险解释与知识引用：

```powershell
.venv\Scripts\python.exe scripts\evaluate_live_agent.py             # 离线评测结构化动作，不调用模型
.venv\Scripts\python.exe scripts\evaluate_live_agent.py --live-llm  # 真实模型评测并生成报告
```

评测集位于 `evals/live_agent_cases.json`，报告写入 `docs/evaluations/`。真实模型模式会产生 token 消耗；无巡检场景与线上一致，直接使用固定提示，不调用模型。

**前端也有一条零依赖的测试链**（`node:test`，不用装任何东西，与后端测试相互独立）：

```bash
cd frontend
npm test                       # = node --test "src/**/*.test.js"
```

它只覆盖从 `.vue` 里抽出来的**纯逻辑**（`src/utils/` 五个模块：答案性质映射 /
导出 Markdown / 记忆开关 / 趋势图坐标 / Chat 载荷与消息整形），**不测组件渲染与交互** ——
那是另一条防线（dev-only 的 SSE mock，见「已知限制」）。**改了 `src/utils/` 下任何文件，
提交前跑一次它。**

### 🔴 改了模型或迁移之后，必须多做一步

```bash
RUN_MYSQL_INTEGRATION=1 pytest tests/test_migrations.py -v   # 别跳过
alembic upgrade head                                         # 把真库推到代码 head
```

**`pytest -q` 全绿不算过。** 原因是：测试库是 **SQLite 内存库 + `create_all` 照模型现建**，
它永远与模型自洽 —— 所以它抓不到"代码 head 与真库版本不一致"这件事。
`test_migrations.py` 里的 drift 用例就是为此存在的，但它默认跳过（要连真 MySQL）。

这不是理论风险：2026-10-13 有 5 个 commit 就是这么漏过去的 —— 本地 SQLite 375 条全绿，
而真库停在 `6f5b38de538b`、代码 head 已到 `c7e2a94b6d18`，
三个新接口在真库上全是 `Unknown column` / `Table doesn't exist`。
**没在真库跑过的迁移，等于没验证过。**

---

## 接口一览

所有接口前缀 `/api`，统一响应体 `{"code": 0, "message": "success", "data": ...}`
（HTTP 状态码恒为 200，真实业务错误码在 body 里），鉴权用 `Authorization: Bearer <token>`。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/auth/register` · `/auth/login` · GET `/auth/me` · `/auth/config` | 注册 / 登录 / 当前用户 / 公开配置（`allow_register`，登录页据此显示注册入口）<br>⚠️ **`/auth/register` 默认关闭**（`AUTH_ALLOW_REGISTER`），关着返回 `40300` |
| GET · POST | `/knowledge` | 知识库列表 / 创建 |
| GET · PUT · DELETE | `/knowledge/{id}` | 详情 / 修改 / 删除（级联清理文档、chunk、向量与磁盘文件） |
| POST · GET · DELETE | `/knowledge/{id}/members` | 成员授权 / 列表 / 移除 |
| POST | `/knowledge/{id}/vectorize` | 批量向量化该库全部已完成文档 |
| POST | `/documents/upload` | 上传并后台解析（需该库 write） |
| GET | `/documents` · `/documents/{id}` · `/documents/{id}/status` · `/documents/{id}/chunks` | 列表 / 详情 / 状态（轮询）/ 切分块 |
| POST | `/documents/{id}/vectorize` | 向量化单个文档 |
| DELETE | `/documents/{id}` | 删除文档（连带 chunk、向量、磁盘文件） |
| POST | `/search` | 向量检索（可开关重排，返回权限范围内的结果） |
| POST | `/chat` | **RAG 问答**：返回答案 + 引用来源；带 `conversation_id` 即接着上文问 |
| POST | `/chat/stream` | **RAG 问答（SSE 流式）**：逐字返回，事件 `meta` → `delta`×N → `done`（失败给 `error`）。⚠️ 它**不走统一信封**，且准备阶段的失败仍返回普通信封 —— 见第 10 节 |
| POST | `/agent/chat` | **Agent 问答**：额外返回执行轨迹、检索轮数与答案性质 |
| GET | `/rpa/metrics?days=30` | **运营监控指标**：按用户可见范围汇总任务成功率、失败数、平均处理时长、审批、最新巡检状态和异常明细（`days` 为 1–365） |
| GET · PUT | `/rpa/monitoring-plans` · `/rpa/monitoring-plans/{session_id}` | **直播定时监控**：查看本人计划；为有选品的场次启用、暂停或调整 5–1440 分钟采集间隔。调度器只创建只读采集任务 |
| GET | `/commerce/sessions/{id}/recap` | **单场直播复盘**：汇总选品计划、最新巡检风险、RPA 成功率与耗时、任务明细和审批闭环；按场次负责人隔离，前端可导出 Markdown |
| GET · DELETE | `/conversations` · `/conversations/{id}` | 会话列表（`?limit=` 默认 50、`?offset=` 翻页）/ 详情（默认**最新一页**：`?limit=`、`?before_id=` 往前翻，响应带 `has_more`）/ 删除。**没有"创建"接口** —— 首次提问时隐式建 |
| GET · POST · DELETE · **PATCH** | `/memories` · `/memories/{id}` | **长期记忆**：跨会话记住的事实（列表 / 写入 / 删除 / **改内容或切换单条开关**）。`PATCH` 两个字段都可选但**至少给一个**（都不给是调用方 bug，返 40000）。`is_enabled=false` 只是不拼进提示词，数据仍在列表里 —— 要彻底不要请用 DELETE. 归属校验同会话：别人的 id 一律 404 |
| POST | `/chat` · `/chat/stream` · `/agent/chat` · `/agent/chat/stream` | **FAQ 闸门**：四个端点进入各自流程前都先查常见问题，命中即返回标准答案（不检索、不调模型）。见「FAQ 的口径」那节 |
| POST · GET | `/messages/{id}/feedback` | **答案反馈**：打分（`rating` = up/down，down 可带 `reason` + `comment`）/ 读自己那条（没评价过返回 `data: null`，不是报错）。**重复提交是 upsert**；只能评 assistant 消息；👍 带的原因会被丢弃（那是矛盾数据） |
| GET | `/chunks/{id}` | **引用溯源**：取一个切分块的**完整**正文 + 文档名 / 页码 / 知识库 / 字数。权限 = 块所属知识库的 read；无权与不存在共用 404 |
| GET · POST · PATCH · DELETE | `/faqs` · `/faqs/{id}` | **常见问题**：列表（`?kb_id=`，read 权限即可）/ 增 / 改 / 删（后三者需库的 **admin**）。上限 `FAQ_MAX_PER_KB=10`；同一库里问题**不允许重复**（create 时查重） |
| GET | `/vector/health` · POST `/vector/purge-orphans` | 向量自检 / 清理孤儿向量（管理员） |
| GET | `/health` | 健康检查（MySQL / Milvus / Redis），逐项 `ok/fail`；3 秒内复用结果 |
| GET | `/stats/overview` | **概览统计**（按当前用户可见范围）：库数 / 文档数（按状态）/ 我的提问数（今日·累计）/ 最近文档与提问。管理员额外给用户数与向量条数（非管理员该段是 `null`，前端整块不渲染） |
| GET | `/stats/trends` | **趋势数据**（概览页图表用，与 overview 口径完全一致）：近 N 天每天提问数（`days` 1–90，**没有提问的日子也补 0**，否则折线断格）、各知识库文档数（按数量倒序） |
| GET · POST · PATCH | `/users` · `/users/{id}/role` · `/users/{id}/status` · `/users/{id}/password` | 用户列表（默认最小字段，`?full=true` 带邮箱与注册时间）/ **开通账号**（可指定初始角色） / **改角色**（管理员，见第 5 节的三条约束） / **停用·启用**（立刻拒登录，已签发 token 下一次请求即失效） / **重置密码**（旧密码与该用户所有登录态一起失效，见第 5c 节） |
| GET | `/users/{id}/grants` | 某人被授权了哪些库（管理员）—— 从"人"这一侧看权限全貌 |
| GET | `/audit` · `/audit/actions` | 管理操作审计（管理员）：**开通账号 / 停用·启用 / 重置密码** / 改角色 / 授权 / 撤销 / 建库 / 删库 / **改可见性**，带操作者与对象名**快照**。支持 `action`、`actor_id` 筛选与 `before_id` keyset 翻页（响应 `{items, has_more}`）；`/audit/actions` 下发动作清单供前端渲染筛选下拉 |

### 错误码分段

HTTP 状态码恒为 200，业务结果看 `code`。**`4xxxx` = HTTP 状态码 × 100**，
`5xxxx` 按依赖组件细分（这是刻意的：LLM 与 Embedding 是**两家服务商两套 key**，
共用一个码时分不清该查哪一个）。

| code | 含义 | 备注 |
|---|---|---|
| 40000 | 参数错误 | 含请求体校验失败 |
| 40100 | 未认证 | 缺 / 无效 token、已过期 |
| 40300 | 无权限 | 已登录但对该资源权限不足 |
| 40400 | 不存在 | **也用于"不是你的"** —— 见下方说明 |
| 40500 | 方法不允许 | |
| 40900 | 资源配额已达上限 | 例如会话数达到 `CONVERSATION_MAX_PER_USER` |
| 42900 | 请求过于频繁 | 触发了限流 |
| 50000 | 服务端通用错误 | |
| 50001 | 文件处理失败 | 解析 / 落盘 |
| 50002 | MySQL 异常 | |
| 50003 | Redis 异常 | |
| 50004 | Milvus 异常 | |
| 50005 | LLM 调用失败 | 对话模型（DeepSeek） |
| 50006 | 工具调用失败 | Agent 侧 |
| 50007 | Embedding / Rerank 失败 | 向量模型（硅基流动，与 LLM 不是同一家） |
| 59999 | 未知错误 | |

> 40400 为什么也用来表示"无权访问"：如果"不存在"和"不归你"给不同的码或不同的文案，
> 遍历 id 比对响应就能探出系统里有哪些资源。同理，会话接口对别人的 id 一律报
> 「会话不存在」。**这是刻意的省略，不是漏了 40300。**

以 `app/core/response.py` 的 `ErrorCode` 与 `exceptions._HTTP_STATUS_TO_ERROR_CODE` 为准。

---

## 几个关键设计

### 1. 权限过滤发生在**检索阶段**，不是查完再筛

允许访问的知识库 id 会被拼成 Milvus 的过滤表达式（`knowledge_base_id in [...]`），
在向量检索时就完成过滤 —— 而不是"先全量检索、再在应用层丢掉没权限的"。
后者既有性能问题，也有把无权内容带进内存/日志的风险。

请求里传的 `kb_ids` 只是"想缩小范围"的意图，会和用户的真实权限**求交集**；
交集为空时**直接返回空结果**，一次 Milvus 都不查 —— 绝不退化成"不过滤搜全库"。

### 2. 两段式检索：召回与精度分工，且**重排失败必须降级**

```
向量召回 RAG_CANDIDATE_K 条（默认 20，保召回率）
   → BGE-Reranker 精排 → 取前 RAG_TOP_K 条（默认 5，保精度）
```

重排服务失败时**退回向量分数顺序继续**，整条问答不会失败 —— 这与 embedding 的处理**相反**：
embedding 挂了就拿不到查询向量、根本无法检索，必须失败；而重排只是"锦上添花"。
响应里带 `reranked` 与未重排原因，能看出这次到底排没排。

### 3. Agent 用状态机：路由（检索 / 文档任务 / 清单 / 直答）与循环都由图表达

这是顺序调用表达不了的东西：它需要**条件分支 + 回到上一步的循环 + 跨轮累积的状态**。
LangGraph 把这三样变成显式的东西 —— 图长什么样，决策逻辑就长什么样，可以被画出来、被测试、被讲清楚。

```
analyze ──SKIP──→ generate → END
   │
   ├─ SEARCH → retrieve → evaluate ──够──→ generate → END
   │                         │
   │                      不够且没到轮数上限
   │                         └──────→ 回到 retrieve
   │
   ├─ DOCS → fetch_docs → generate → END
   │        （取点名文档的全文：总结 / 对比；
   │          没拿到整篇的名字**按名字补检索**（缺的那半边不会静默消失）、
   │          全部落空才回落原句检索 —— 误判的代价约等于零）
   │
   └─ LIST → list_docs → END
            （文档清单："我们都有哪些制度文档" —— 目录是确定性数据，
              节点里直接拼好答案就结束，**不经模型**，与 FAQ 命中即返回同一条思路）
```

**三件工具，对应三种"取数能力"**（不按任务类型切 —— 总结/对比/提取本质都是
"拿到内容 + 用不同提示词生成"，按任务切成多个工具只会重叠；判断标准很简单：
把工具去掉，对应的那类问题就**答不了**，而不是"答得差一点"）：

- `search_knowledge`：语义 + 关键词**混合检索**（问内容，如"年假几天"）；
- `get_document`：按文档名取**全文**（点名文档的任务，如"对比考勤和报销制度"）。
  名字匹配**逐级剥尾缀、逐级试**（"考勤管理制度" → "考勤管理" → "考勤" →
  《员工考勤与休假管理制度》），只取 completed 的文档（处理中的文档命中的是上一轮旧切块），
  同名多份取最新上传的那份；一份整篇都没拿到时**按缺失的名字补检索**
  —— LIKE 匹配是本地查询、零成本，所以"模型多判成 DOCS"的代价约等于零；
  全部落空才回落按原句检索（最后的保底）。
- `list_documents`：列可见范围内的文档清单（元数据查询："我们都有哪些制度文档"）。
  答案由 `list_docs` 节点直接拼出（不经模型），超 50 份截断并如实说明。

**文档任务的预算不变量**：进上下文的正文总量 + 每块标题余量 ≤ `RAG_MAX_CONTEXT_CHARS`。
份数多到单份预算撑不住时**少取几份**（被裁的名字降级为检索片段），而不是抬高单份预算 ——
抬高会悄悄推翻不变量，下游会静默丢掉整块，"对比"就变成"只看第一份"。
未命中 / 被裁 / 被截断 / 有歧义的名单进 `doc_notes`，生成时**如实告知用户缺了什么**。

⚠️ 两组"不短路"的词：**文档任务词**（对比/总结…）与**清单问法**
（哪些×文档/文件/资料）命中时**不走业务词的规则捷径**，交给模型区分 ——
否则"我们都有哪些**制度**文档"会被"制度"这个词短路成 SEARCH，LIST 分支永远走不到（实测踩过）。

同时设了**三道成本闸门**（Agent 比纯 RAG 多调几次模型）：

1. `AGENT_MAX_ITERATIONS` —— 最多检索几轮，到顶强制生成；文档任务另有：最多取 3 份、预算不变量（见上）；
2. 判定节点输出极短（`max_tokens` 8~32）—— 一个指令以内，不给它长篇大论的机会；
3. `recursion_limit` —— LangGraph 自带的兜底。

另外两条降级方向是**刻意相反**的：判定节点（analyze）失败 → 默认**去检索**（宁可多花钱也不凭空答）；
评估节点（evaluate）失败 → 默认**收手**（不因为解析失败反复烧钱）。

### 3b. 检索为什么要加关键词路（混合检索 + RRF）

纯向量检索是**语义匹配**，对"编号 / 型号 / 专有名词"（ISO27001、SQL-2000）不敏感 ——
字面精确出现的内容反而不容易被召回。这是向量检索的已知短板，不是调参能解决的。

所以检索走**双路召回**：

- **向量路**：BGE-M3 → Milvus 余弦召回（语义）；
- **关键词路**：jieba 提词 → MySQL **FULLTEXT ngram** 索引召回（`MATCH ... AGAINST`），
  按命中词数排序。2026-10-15 从 LIKE 全表扫升级为全文索引（迁移 `d9e4f1a2b3c5`）——
  块数上到几万后逐行扫会肉眼可见地慢；SQLite 测试环境没有这个特性，repo 按方言
  分派、继续走 LIKE 分支，两套排序口径逐字一致。
  **召回语义也一致（完整词命中才进候选）**：ngram_token_size=2 会把查询切成二元组，
  NATURAL LANGUAGE MODE 任一片段命中即召回 —— 于是"只共享 2-gram、不含完整词"的块
  也会带着 hit_count=0 混进来（2026-10-15 评审实锤 + 真库复现：查 ISO27001 曾召回
  9 行、8 行 hit=0）。这些 0 分行会参与 RRF、把向量路真命中挤出 candidate_k，
  正好打在"关键词路补编号/专名词短板"的立项目标上。修法：MySQL 分支返回前
  按 `hit_count>0` 过滤（INSTR 已经在算，零成本）；LIKE 的 OR 条件天然保证 ≥1，不用过滤。

两路候选按 **RRF（Reciprocal Rank Fusion）** 融合：只看得名次、不看分数 ——
两条路的分数不可比（余弦相似度 vs 词项覆盖率），归一化加权要拍脑袋配权重；
RRF 是"两路都靠前就最靠前"的无参融合，工程上最稳。

四个工程细节：

1. **jieba 需要领域词表**：通用词典把"年假"切成"年 / 假"（实测）——
   建了一份制度领域词表（假期 / 薪酬 / 考勤 / 流程…），初始化时 `add_word`，
   关键词路对业务词才真正可用；词表构建在服务启动时预热（`warmup`），
   不让第一个提问白等那一次 ~0.8s；
2. **关键词路空跑对融合透明**：提不出词（全是虚词）或没命中就返回空，
   融合自动退化成纯向量原顺序 —— 这条路的失败模式是"没有贡献"，不是"拖垮检索"；
3. **权限与状态口径和向量路完全一致**：两路都只认 completed 文档、
   都用"与用户权限求过交集"的 kb 范围（关键词路自己不做权限判断，范围由检索层传入）；
4. **召回来源可分辨**：每条结果带 `retrieval_path`（vector / keyword / both）——
   仅关键词命中的分数是**词项覆盖率**（不是余弦相似度），前端把它显示为「关键词匹配」，
   不混叫"相似度"；
5. **MySQL 分支只有真库用例保护**：SQLite 没有 FULLTEXT ngram，所以默认跑的全量测试
   全跑在 LIKE 分支上 —— **改关键词路必须 `RUN_MYSQL_INTEGRATION=1 pytest
   tests/test_mysql_hybrid.py -v`**（那里钉着召回 / 排序 / 范围 / 2-gram 过滤四条）。

开关：`RAG_HYBRID_ENABLED`（默认开）；关掉即退回纯向量（对照排查用）。
`tests/test_hybrid.py` 钉着最关键的一条：**向量路全空时（Milvus 故障 / 重灌前），
关键词路能把字面命中的内容兜住** —— 以及它不会成为权限旁路。

### 3c. 性能基准

`scripts/benchmark.py` 把"系统有多快"变成可复现的数字（分段计时，与线上同一条链路）：

```
.venv\Scripts\python.exe scripts\benchmark.py            # 全链路（需 LLM API 可达）
.venv\Scripts\python.exe scripts\benchmark.py --skip-llm # 只测检索 + 重排
```

2026-10-15 实测（24 次完整链路，串行单用户）：检索段 P50 ≈ 100ms、
重排段 P50 ≈ 1.1s（瓶颈在硅基流动 rerank API）；生成段当时 DeepSeek API 不可达
未测到，报告 `docs/benchmark_2026-10-15.md` 如实标注。数字随负载波动，
以最新一次运行为准，旧报告留档做历史对照。

### 4. Milvus 只存向量与定位字段，正文留在 MySQL

collection 里只有 `chunk_id`（主键）/ `document_id` / `knowledge_base_id` / `page_number` / `vector`。
用 MySQL 的 chunk_id 当主键还有个好处：**写入天然幂等**，重复向量化不会产生重复数据。
检索命中后回 MySQL 批量取正文与文件名（一条 SQL，不是 N+1）。

### 4b. 「向量在不在」和「检索读不读得到」是两件事，界面上必须分开

2026-10-14 实测到的一个真实故障：Milvus 2.6.15 上集合状态是 `Loaded`，`search()` 与
`count(*)` 却一律返回空，而那 10 条向量用**主键点查**查得到（`chunk_id 1–10` 与 MySQL 完全对齐）。
用户侧的表现是"文档明明向量化过，提问却说没找到；**重新向量化一次立刻正常**"——
之所以"立刻正常"，是因为那次写入顺带把数据变成了可读状态，而不是数据被补上了。

当时的自检因此报"向量总数 0、3 篇文档全缺向量"，把人引去重灌**根本没丢**的数据。根因是
`count()` 用 `except: return 0` 把读失败压成了 0 —— "读不到"和"真的是空的"在界面上完全一样。

现在的口径（三条，都有用例钉着）：

- **读失败一律抛，不返回 0/[]**。`count()` / `stats_row_count()` / `list_kb_ids()` /
  `list_document_ids()` 失败即 `AppException`；概览页那一格显示"—（Milvus 不可用）"，
  自检把失败项记进 `read_errors` 并让 `visibility=unknown`。
- **两个读数配对看**：`vector_count`（检索可见）与 `stored_rows`（服务端元数据行数，
  含已删除未压缩的行，实测见过 10 / 37，所以只用来判"有没有数据"，绝不比相等）。
  可见 0 而 stored>0 → `visibility=not_visible`，前端明写**"不要重新向量化"**。
- **写完就 `flush()`，启动时补一次 `load_collection()`**（幂等）。回执带 `flushed` 字段，
  落盘失败时消息里直接说"未落盘，重启后可能检索不到"；启动加载失败**不阻断启动**
  （Milvus 挂了时登录和文档管理仍要能用），只留一条 warning。
  **删除同样要落盘** —— 不落盘的删除记录重启后可能没生效，已删文档的向量会"复活"
  成命中却取不到正文的孤儿。所以三条删除链路（重处理 / 删文档 / 删知识库、清孤儿）都 flush，
  其中批量清理只在结束时 flush 一次。

**同实例里的陌生集合会被点名而不是被忽略**：`foreign_collections()` 用主键字段名
（`chunk_id`）判结构归属。实测那个 Milvus 里躺着一个 10 行、字段只有 `id`+`vector` 的 `docs`
集合 —— "看到有向量"因此把人引向过错误结论。它不属于本应用，所以**不删**，只在自检里
写清楚"非本应用结构，不参与检索"。

实测收口（同一套数据、修复前后）：修复前 `count()=0 / stored=27 / 报"3 篇缺向量"`；
flush + load 之后 `vector_count=10 / stored_rows=10 / expected_chunks=10 / visibility=ok`，
`foreign_collections=['docs']`。

### 5. 审计与业务解耦

`agent_logs` 记录每次 Agent 运行的轨迹、token 与耗时。它由 service 层单独提交 ——
写日志失败会被吞掉并返回 `None`，**绝不让用户拿不到已经生成好的答案**。

`audit_logs` 是另一张表、**相反的立场**：它记的是改角色 / 授权 / 撤销 / 建库 / 删库
这些"能改变谁能看到什么"的管理动作。它**不允许静默丢失** —— 写失败会让整个改动一起
失败（同一个事务），因为一次没有记录的角色变更，事后根本无法追责，等于没有审计。

两条设计上的细节：
- **操作者与对象都存名字快照**（`actor_name` / `target_name`）。用户被改名或删除之后，
  "2026-10-12 张三把李四改成 admin" 这句话必须仍然读得懂 —— 只存 id 的话一旦对应的
  行没了，审计就变成一串无意义的数字。
- **`actor_id` 刻意不加外键**：加了之后"删除一个用户"会直接被外键挡住，而审计恰恰是
  最不该因为业务数据被删而消失的东西。代价是这一列可能悬空，查询要容忍。

**覆盖边界**（写清楚，免得有人以为这张表是全的）：

| | 记 | 不记 |
|---|---|---|
| 权限与可见性 | 改角色、授权 / 撤销、建库、删库、**改公开/私有** | |
| 内容级 | | 删文档、单文档 / 整库向量化 —— 改变的是"能检索到什么"，但它们由 `documents` 表自身记录归属，且高频（一次向量化几百条向量），记进来只会淹没真正的权限变更 |
| 元数据级 | | 改知识库名称 / 描述 —— 不影响"谁能看到什么" |

⚠️ **`is_public` 是权限开关，不是元数据**：置 True 之后所有登录用户至少有 read，
而且**下一次请求就生效**（`get_accessible_kb_ids` 立刻把它算进检索范围）——
比改角色生效还快（改角色要等下次登录）。所以它必须留痕，且存 `public/private`
而不是 `true/false`（几个月后翻审计的人不该需要对照代码猜布尔值的意思）。

### 5b. 用户生命周期与"别把自己锁在门外"

账号的产生与终止都归管理员，只有一条自助路径（且默认关）：

| 环节 | 入口 | 说明 |
|---|---|---|
| **开通** | `POST /users`（管理员） | 可**一开始就指定角色** —— 否则建完还得再调一次改角色，中间那一步"新账号已有 employee 权限"是个没必要的窗口 |
| 自助注册 | `POST /auth/register` | ⚠️ **默认关**（`AUTH_ALLOW_REGISTER`）。关着时登录页不渲染注册入口，接口返回 `40300` |
| **改角色** | `PATCH /users/{id}/role`（管理员） | 提权入口，见下 |
| **停用 / 启用** | `PATCH /users/{id}/status`（管理员） | 立刻拒登录，**已签发的 token 下一次请求即失效** |
| **重置密码** | `PATCH /users/{id}/password`（管理员） | 旧密码与**该用户全部登录态**一起失效。**允许重置自己**（不构成自锁） |
| 删除 | —— | 刻意不做：会牵连会话与审计记录（外键级联），而停用能覆盖绝大多数需求且可回退 |

**"不能把自己锁在门外"是一族约束**，两个写入口各有两条：

| 约束 | 防的是什么 |
|---|---|
| 不能改自己的角色 | 唯一的 admin 把自己降成员工 → 再也没人能改回来（除直接改库） |
| 不能停用自己的账号 | 同上，换一条路自锁 |
| 不能让 admin 归零（改角色） | 两个 admin 互相降级 → 系统里没有管理员 |
| 不能让 admin 归零（停用） | 同理，停用是"从另一个方向"减少管理员 |

⚠️ **后两条在 HTTP 层永远触发不到**（要降级/停用 A 就得有 admin B 操作，那时系统里至少两个
admin）。它们是纵深防御，挡的是"将来某个直接调 service 的场景"。测试因此绕过接口层直接
调 service 来验证 —— 这些"跑不到的代码"都有注释说明，别当成死代码删掉。

⚠️ 而且它们**必须先加锁再数**（`count_admins(..., for_update=True)`）：`SELECT COUNT(*)`
不加锁时，两个并发请求各自看到 2、各自都通过 → 真的会归零。注意"更新后再复查一次"
**不是**等效修法 —— MySQL 默认 REPEATABLE READ 下一致性读用的是事务开始时的快照，
复查仍会看到 2，要复查也只有加锁读才有效。
⚠️ 这条修复**在测试环境无法用行为验证**（SQLite 不支持 `SELECT ... FOR UPDATE`，
SQLAlchemy 会静默忽略它 → 并发场景永远跑不出来），所以用一条"编译 MySQL 方言后
SQL 里必须有 `FOR UPDATE`"的静态断言钉着 —— 防止它在重构时被悄悄删掉而测试全绿。

### 5c. 停用 / 改角色 / 改密码，为什么都能"立刻"生效

JWT 是无状态的，服务端**不吊销**已签发的 token（见「已知限制」的「登出语义」）。
这是无状态的代价。但"不吊销"不等于"改不了"—— 本项目对**三种改变权限的动作**
各有一个立刻生效的机制，共同点是**不在 token 里存信任**：

| 动作 | 立刻生效靠什么 |
|---|---|
| **停用账号** | `login` 与 `get_current_user` 都实时回库查 `is_active` → 拒 |
| **改角色** | `get_current_user` 实时回库读 `user.role`，**不信任 token 里的 `role`** |
| **改密码** | token 里带**密码指纹**（`security.password_fingerprint`），鉴权时与当前 `password_hash` 比对 |

**停用**那两道是独立的：`login`（下次登录进不来）与 `get_current_user`（手上那张票立刻作废），
测试分开钉 —— 去掉任一道只有对应的那条用例变红。少了第二道，"停用"就只是"下次登录进不来"，
已登录的会话还能一直用到 token 过期（默认 24 小时）。

**改密码**为什么需要额外机制：前两种靠"实时查一个布尔/外键"，而"密码改没改"没有现成的比对目标 ——
于是把 `password_hash` 的短指纹（sha256 前 16 位）写进 token，鉴权时比一次。
bcrypt 每次换盐，所以**任何**改动 `password_hash` 的路径都会让旧 token 自动作废，
不必在每处记得手动清理。

⚠️ 指纹**可公开**：它取自 bcrypt 哈希（单向、抗反推），算出来也推不出密码，
泄露 token 不比泄露哈希更糟。⚠️ 口径是**严格**的：不带这个 claim 的 token 一并拒掉 ——
本项目还没上线，宽松放行会留下"老 token 永久有效"的洞。

⚠️ 反面对照也被钉住了：**同一密码重新登录不会踢掉别的设备**（指纹取自哈希而非签发时间），
多设备登录照常。

### 6. 关闭对话模型的思考模式

`deepseek-flash` 默认开启思考（思考内容同样计费）。实测同一个问题：
不关思考 `completion_tokens = 36`，关闭后 **6** —— 省约 83%，而 RAG 场景的上下文更长，
收益更明显。关闭方式是 `extra_body={"thinking": {"type": "disabled"}}`
（注意不能用 `model_kwargs`，那会把它当成顶层参数传给底层 SDK 而报错）。

---

## 实测数据

以下数字来自本机真实运行（真实 Milvus + 真实向量 / 重排 / 对话 API），非估算。

### Agent 并不总是更好 —— 关键结论

| 问题类型 | 纯 RAG（`/chat`） | Agent（`/agent/chat`） | 结论 |
|---|---|---|---|
| 业务问题（"年假可以休几天"） | 349 tokens | 654 tokens（1 轮检索） | 答案完全一致，**Agent 贵 87%** |
| 寒暄（"你好，你能做什么"） | 308 tokens，**照样去检索了**，回答别扭 | 205 tokens，`iterations=0` 跳过检索，回答自然 | ✅ **Agent 更好且更省** |
| 库里没有的内容（"公司食堂几点开饭"） | 303 tokens | 840 tokens（2 轮） | 答案都是"无法回答"，**Agent 贵 2.8 倍** |

**结论：Agent 的价值在「判断该不该查」，而不是「查得更准」。**
所以没有无脑全量切 Agent —— `/chat` 与 `/agent/chat` 两个接口并存，各自适用不同场景。
（也正因为这条，业务疑问词会**由规则强制去检索**：一旦模型把业务问题误判成寒暄，
那条直答路径不经过评估节点，没有任何机制纠正它。）

### 长期记忆会不会盖过资料？（实测：不会）

长期记忆拼在**系统提示词尾部**（`SYSTEM_PROMPT + "\n" + 记忆段`），而它的内容**是用户自己写的** ——
位置又在"近因"处，所以理论上存在"记忆里的说法压过检索到的资料"的风险。
结构上已经用表头约束（"仅用于理解背景、不能当作制度依据、不要为它们标注来源编号"），
但那只是**推测**；下面是实测（临时库放一篇写着"年假 5 天"的文档 → 向量化 → 再写一条
**矛盾**记忆"本公司年假一律 30 天" → 同一个问题各问一次；跑完库 / 向量 / 记忆 / 会话全部清理）：

| | 答案 | 引用 | tokens |
|---|---|---|---|
| 关记忆（对照） | 年假 **5 天**，最多结转 3 天 | `员工手册.txt`（0.7496） | 297 |
| 开记忆（实验） | 年假 **5 天**，最多结转 3 天 | `员工手册.txt`（0.7496） | 329 |

**记忆里的"30 天"没有出现在答案里** —— 检索到的资料赢了，代价是 **+32 tokens**。
（诚实标注：这是**一次**探针，不是分布；换更对抗的措辞、或让检索命中变弱，结论未必相同。
结论写在这里是为了把"我们想过这件事并测过"变成证据，而不是拿它当一般化的保证。）

### 重排的价值：把"勉强召回"的噪声压下去

| 查询 | 纯向量 Top1 | 重排后 |
|---|---|---|
| 年假可以休几天 | 员工手册 0.7498 / 考勤制度 0.6420 | 员工手册 **0.9239** / 考勤 0.5047 / 团建通知 0.2915 |
| 请假要走什么流程 | 考勤制度 0.7579 | 考勤 **0.8544** / 员工手册 0.0077 / 团建 0.0003 |

重排能把无关内容（字面撞词、实际跑题）压到接近 0，这是向量相似度做不到的。

### 向量模型实测（选型依据）

| 项 | 实测 |
|---|---|
| 单条 / 批量延迟 | 单条约 1s；**200 条仅 2.7s** —— 延迟不随条数线性增长，所以按批送（默认 64 条/批） |
| 语义区分度 | 同义句余弦 0.88，无关句 0.38（向量确实带语义） |
| 确定性 | 同文本重复调用余弦 = 1.00000000（无需本地缓存兜底） |
| 超长失败模式 | 超过 8192 token **明确报错**（不会静默截断后半段） |
| 空文本 | 会返回"合法但无意义"的向量 → 代码必须主动跳过空白 chunk |

---

## 项目结构

```
.
├── main.py                 # 入口（uvicorn main:app）
├── app/
│   ├── api/                # 路由 + 依赖（鉴权、限流、分页）
│   ├── services/           # 业务编排 / 事务边界
│   ├── repositories/       # 数据访问（只 flush）
│   ├── models/             # SQLAlchemy 模型
│   ├── schemas/            # Pydantic 请求 / 响应模型
│   ├── rag/                # 解析、清洗、切分、向量化、混合检索（关键词/融合）、重排、提示词、模型客户端
│   ├── agent/              # LangGraph 状态机（state / tools / graph）
│   ├── automation/         # RPA 执行层：页面协议 / 采集编排 / Playwright / 队列 worker / 定时调度器
│   ├── db/                 # Engine、Session、建表与初始化
│   ├── core/               # 配置、异常与错误码、统一响应、日志、限流
│   └── utils/              # 文件落盘与校验
├── frontend/               # 正式前端（Vue3 + Vite + Element Plus）
│   ├── src/api/            #   接口封装（路径只在这里出现一次）
│   ├── src/stores/         #   Pinia（登录态）
│   ├── src/router/         #   路由 + 登录守卫
│   ├── src/views/          #   运营工作台 / 直播任务 / 自动化任务 / 知识库 / 运营助手等
│   ├── src/utils/          #   纯逻辑 + node:test 用例（`npm test`，见「快速开始」）
│   └── src/layouts/        #   侧边栏 + 顶栏
├── artifacts/rpa/          # RPA 截图证据（按任务分目录；不入库、不进 Git）
├── devtools/index.html     # 单文件后端调试台（开发自测）
├── docs/                   # 设计与实验记录（benchmarks/ 存对照实验的语料、数据与结论）
├── tests/                  # 单元测试 + 可选的集成测试（Milvus / Redis / MySQL）
├── migrations/             # Alembic 迁移脚本（全新环境也要靠它，见下）
├── alembic.ini             # Alembic 配置（连接串不写这里，由 migrations/env.py 从 .env 读）
├── pytest.ini              # pytest 配置
├── .env.example            # 配置模板（只放占位符）
└── requirements.txt
```

### 「RPA 自动化」怎么跑（阶段三）

四个进程/入口，调度器和 worker 通过 Redis 队列 `enterprise-agent:rpa:tasks` 解耦：

```bash
# 1) 主服务（含模拟电商后台 /mock-platform，RPA 用浏览器访问它）
python -m uvicorn main:app --port 8000

# 2) RPA worker（独立进程；首次需装浏览器二进制：playwright install chromium）
python -m app.automation.worker

# 3) 定时监控调度器（配置监控计划后必须启动；默认每 15 秒扫描到期计划）
python -m app.automation.scheduler

# 4) 前端（直播任务页可手动采集；自动化任务页配置定时监控）
cd frontend && npm run dev
```

也可以在项目根目录用一个终端启动完整开发栈。脚本会先执行迁移，API 就绪后再启动
Worker、Scheduler 和前端；按一次 `Ctrl+C` 会统一停止子进程：

```powershell
.venv\Scripts\python.exe scripts\start_stack.py
```

首次运行 Playwright 仍需安装浏览器：

```powershell
.venv\Scripts\python.exe -m playwright install chromium
```

设计要点（都是可被追问的）：
- **采集编排与浏览器解耦**：`automation/collector.py` 只依赖 `page.py` 的协议，单测用 `FakePage`
  跑完整链路（`tests/test_rpa_worker.py`，6 条），不需要真开浏览器；
- **每一步都留证据**：`rpa_task_steps` 记步骤/时间/状态/说明 + 截图路径，
  截图按任务分目录存 `artifacts/rpa/<task_id>/`，经**带归属校验**的端点下载
  （不做静态目录挂载：那等于把"谁的证据"交出去）；
- **幂等与并发**：任务有唯一 `idempotency_key`；worker 用 Redis `SET NX` 锁防多开时重复执行同一任务；
- **定时采集可追踪**：每个场次一条监控计划；调度器锁定到期记录，以“计划 ID + 计划执行时间”生成
  确定性幂等键，推进下次执行时间并记录最近任务；场次结束后计划自动停用；
- **重复告警抑制**：相同场次、任务类型和失败原因的异常合并展示次数，仍保留最新任务作为处理入口；
- **复盘口径来自真实记录**：单场报告只汇总选品、巡检、RPA 和审批表中的已有事实，并明确标注
  尚未接入订单/退款数据；不使用装饰性成交额或虚构业务指标。直播任务详情可查看并导出 Markdown；
- **部分成功不伪装成失败**：平台缺某个 SKU 时记失败步骤 + `result.missing_sku_codes`，
  其余照常写快照；一个都没采到才整体失败；巡检失败不连坐采集（快照已落库）。
- ⚠️ 包名是 `app/automation/` 而不是 `app/rpa/`：与 `app/api/rpa.py` 同名时，
  `import app.rpa` 会在 `app` 命名空间里覆盖 `rpa` 全局名，导致 `include_router(rpa.router)`
  在"先导入包"的进程里拿到包而不是 api 模块（pytest 与 worker 启动时会炸，单独跑服务却正常）。
- ⚠️ **新增的 RPA 三张表最初漏了迁移**（模型写了、迁移没生成，真库 1146）。
  已补 `e9c2b7d14a58`；`tests/test_migrations.py` 的 drift 检查现在也能跑了 ——
  同时把 FULLTEXT ngram 索引加进了它的忽略清单（模型表达不了 `WITH PARSER ngram`）。

### 运行检查、故障恢复与完整演示

运行时检查会验证 HTTP 健康状态、真实数据库迁移版本、Redis 队列深度、入队失败任务、
超过 15 分钟仍在运行的任务、过期监控计划和 Redis 过期消息。`--strict` 适合部署验收或 CI：

```powershell
.venv\Scripts\python.exe scripts\check_runtime.py
.venv\Scripts\python.exe scripts\check_runtime.py --strict --json
```

恢复工具默认只预览。它只自动处理 Redis 入队失败任务，以及没有 Worker 锁且超时的只读采集任务；
失败的改价/下架任务不会自动重跑。它也会清理数据库任务已结束、但 Redis 中仍残留的过期消息：

```powershell
.venv\Scripts\python.exe scripts\recover_rpa_tasks.py
.venv\Scripts\python.exe scripts\recover_rpa_tasks.py --apply
```

首次展示前先初始化完整的旅游直播业务数据。脚本会创建演示账号、3 个知识库、10 份已处理并
向量化的旅游资料、6 个商品、8 个 SKU，以及“可开播 / 待处理 / 已结束”三种直播场次：

```powershell
.venv\Scripts\python.exe scripts\seed_tourism_demo.py --reset --remove-legacy
```

演示账号为 `demo_operator / Demo@123456`，登录页也提供“一键填入”。脚本只清理固定标识的
演示数据，不会删除自行录入的业务数据。若 Milvus 或 Embedding 服务暂不可用，可临时追加
`--skip-vectors`，但此时知识问答不能展示向量召回。

需要现场展示真实浏览器自动化时，再启动完整进程并运行 RPA 脚本。它会创建酒店套餐异常场次：
计划销量 30、可售房量 8，计划价 1999 元、平台直播价 2199 元；随后由 Worker 完成浏览器采集
和自动巡检：

```powershell
# 终端 1
.venv\Scripts\python.exe scripts\start_stack.py

# 终端 2
.venv\Scripts\python.exe scripts\demo_live_rpa.py
```

脚本成功后，前端按“直播任务 → Agent 解释 → 生成审批草稿 → 管理员审批 → 自动化任务证据 →
单场复盘”完成演示。演示数据写入当前 MySQL，重启后仍可继续展示。

### 「高风险操作审批」怎么走（阶段四）

采集是只读；**改价 / 下架是写**，必须先有审批单。完整链路：

```
巡检发现阻断（如"计算到手价与计划价不一致"）
  → 运营发起申请（目标 SKU + 预期值 + 理由）
  → 负责人审批（申请人不能批自己的单）
  → 通过 = 生成**唯一** RPA 修复任务（幂等键复用审批单）
  → worker 执行：改价 / 下架
  → **重新读平台验证**（值不等于预期 = 失败，不是成功）
  → 审批单 executed / failed
```

四条设计约束（都可被追问）：

- **审批单绑定"目标 + 预期值 + 幂等键"**：执行前用它校验目标，执行后拿它验证结果；
  任务表 `idempotency_key` 唯一 → 重复点通过、worker 重试都不会产生第二次改价；
- **预期值在入口规整**：`99` / `99.0` / `"99.00"` 落库统一为 `99.00`——
  审批时看到的数字就是执行时用的数字，两处各转一次就可能一处转错；
- **申请人不能自批**（四眼原则，40300）；审批人是场次负责人或管理员（与巡检同口径）；
- **"改完不算完"**：写操作后必须重新读页面按预期值比对，验证不过 → 任务 failed +
  审批单 failed 等人接管（`tests/test_rpa_worker.py::test_repair_verification_failure_raises`
  用 `ignore_writes` 模拟"点了保存但平台没生效"）。
- 同对象同类型只允许一张未结单（pending/approved），已 executed/failed 的可重新申请。


### 7. 多轮对话：历史只带「问答对」，不带当时检索到的资料

每轮问答都会落到 `conversations` / `messages`，下一轮把最近 `CONVERSATION_MAX_HISTORY_TURNS`
轮的问答对拼进提示词。两个刻意的取舍：

- **不带当时的参考资料** —— 否则每轮的检索结果都会堆进上下文，token 随轮数平方级上涨；
  而"这次该看哪些资料"本来就该由本轮检索重新决定。
- **只按「轮」裁，不按「条」裁** —— 截断点落在轮次中间时，宁可丢掉那半条，也不给模型
  一个没有对应问题的孤立答案。配 `CONVERSATION_MAX_HISTORY_TURNS=0` 可关掉历史，
  用来对比"多轮到底有没有用"。
- **无依据的轮次整轮不进历史** —— 「知识库中没有找到…」这类固定话术若以 assistant 身份
  反复喂回模型，模型会顺着那个口径走（越沿用越错）。判据取 `meta.hit`（寒暄直答同样排除），
  实现上"多取几倍再筛"，保证筛完之后仍凑得满配置的轮数。

⚠️ 别把两件事混为一谈：`CONVERSATION_MAX_HISTORY_TURNS` 管的是**送进 Prompt 的范围**
（数据仍在库里、随时回看，**可逆**）；`CONVERSATION_MAX_MESSAGES` 管的是**库里留多少**
（真的删，**不可逆**）。前者替代不了后者，反之亦然。

会话是**隐式创建**的：前端直接开问，后端建好并把新 id 放回响应，下一轮带上即可 ——
少一个"新建会话"按钮，也少一次可能失败的请求。

`/chat` 与 `/agent/chat` **共用同一套会话表**：可以在同一段对话里轮流用
（纯 RAG 答一轮、再让 Agent 接着答一轮，上下文是连着的）。

**撤销某个库的授权，不会让历史里的答案继续可读** —— 会话详情回放时会按当前权限脱敏：
引用了"已无权访问"的知识库的答案**整条隐藏**（正文与引用一起换成提示语），
而用户自己的提问与会话标题保留。口径是"**收回资料，不收回用户自己敲的字**"。

判据查三条线索（2026-10-15 补齐，两条都曾是真实缺口）：

- `sources[].knowledge_base_id` —— 普通答案的引用列表；
- `meta.catalog_kb_ids` —— 文档清单没有 sources，但正文带着【库名】+《文件名》；
- `meta.faq_kb_id` —— FAQ 标准答案没有 sources，但答案出自某个库。

三者都是"答案**真正触及**的库"，与 `meta.searched_kb_ids`（检索范围）刻意分开判：
在三个库里搜、只命中一个，另两个日后再撤权不该连坐这条答案。

⚠️ **历史消息兼容**：`faq_kb_id` / `catalog_kb_ids` 是 2026-10-15 才引入的键，
修复前落的库那两类消息没有它们 —— 判定处对旧形状**回退读取**（仅这两类）：
FAQ 读 `searched_kb_ids` 单元素（恒为 `[faq.kb_id]`）；catalog 读 `searched_kb_ids`
（list_docs 的范围，而清单列的就是 scope 内全部文档——**范围即引用**）。
普通 RAG 消息没有回退，原因就是上面那句"不该连坐"。

⚠️ 已知限制：**没做指代消解**。历史只是照原样拼进提示词，所以"那它有几天"这种省略主语的
追问，检索仍按字面去搜。要做对需要先让模型把问题改写完整（query rewriting）再检索。

---

### 8. 数据库结构只有一条权威：Alembic 迁移

`create_all()` 只会**建表**，不会改表 —— 加个字段、改个类型，它一点忙都帮不上。
所以 Phase 11 引入了 Alembic，并且**没有留第二条路**：建表与改表都走迁移。

```
alembic revision --autogenerate -m "说明"   # 改了模型之后
alembic upgrade head                         # 应用
```

启动流程（`app/db/init_db.py::_ensure_schema`）只**校验**表齐不齐，缺表就报错提示先跑迁移。

> 为什么**不**保留 `create_all` 兜底（哪怕是给 dev 用）：曾经按 `APP_ENV` 门控过——dev/test
> 建表、其余校验。但 `create_all` 造出来的库**没有 `alembic_version` 记录**，
> 于是之后第一次 `alembic upgrade head` 必然撞 `table already exists`：
> 等于亲手把"两个权威"的架吵起来，正是引入 Alembic 想避免的事。一条路最省心。

⚠️ **改了模型却忘了生成迁移**，`autogenerate` 的 diff 仍然显示"已同步"（它比的是「库 vs 模型」，
不是「迁移 vs 模型」），迁移于是变成摆设，直到在别人的机器上炸开。`tests/test_migrations.py`
钉这件事（drift 比对要连真实 MySQL，**默认跳过**）：

```bash
RUN_MYSQL_INTEGRATION=1 pytest tests/test_migrations.py -v
```

两个实测踩到的坑，都写进了代码注释：

1. **基线必须带真实建表语句，不能只 `stamp`。** 最初 9 张表先于 Alembic 存在，
   所以基线做成了空迁移 + `stamp head`。它在老库上没问题，但**在真正的空库上会失败**——
   下一份迁移给 `user_memories` 建外键时找不到 `users`（实测
   `(1824, Failed to open the referenced table 'users')`），也就是"clone 下来先跑
   `alembic upgrade head`"根本走不通。现在基线带上了那 9 张表的完整 DDL：
   老库版本号已 stamp 在它上面（不会重跑），空库能一路建到底。**已用一个临时空库验过
   `upgrade head`（11 张表）与 `downgrade base`（只剩 `alembic_version`）的完整往返。**
2. **自动生成的 `downgrade()` 在 MySQL 上可能是坏的。** autogenerate 会写成
   「先 `drop_index(ix_user_memories_user_id)`、再 `drop_table`」，而那条索引正是外键
   `user_memories.user_id → users.id` 在用的索引，MySQL 直接拒绝
   （`1553, Cannot drop index ... : needed in a foreign key constraint`）——
   `alembic downgrade` 必然失败，还会把 `alembic_version` 卡在一个跑不完的迁移上。
   **删表本身会连带删掉它的索引与外键**，所以 `downgrade()` 里一行 `op.drop_table(...)` 就够。
   （通用经验：**外键列上的索引，MySQL 不允许单独 drop**。每份新迁移的 `downgrade()`
   都值得实测一次往返。）

---

### 9. 长对话摘要：**增量**压缩，不是每轮重总结

喂给模型的历史只有最近 `CONVERSATION_MAX_HISTORY_TURNS` 轮，更早的**直接丢**。
Phase 12 加了一段**滚动摘要**（`conversations.summary`）把丢掉的部分压成一段话。

三个决定性的设计：

- **增量，不重头**：`summary_upto_id` 记住"摘要已覆盖到哪条消息"，下一次只把**新滑出窗口**的
  那几轮合并进去。每轮重喂整段对话的话，token 会随轮数**平方级**上涨 —— 那还不如不加。
- **机会式触发**：挂在写入路径上（和 agent_logs 的过期清理同一思路，项目没有调度器），
  且要**攒够** `CONVERSATION_SUMMARY_TRIGGER_TURNS` 轮才做一次，否则每轮都要多调一次模型。
- **失败不动字段**：摘要调用挂了就只 warning，`summary` / `summary_upto_id` 原样留着，
  下一轮再试。写半个摘要进去或推进覆盖位置，都会让那几轮内容**永久丢失**。
- **必须先于裁剪**：`append_turn` 里摘要在 `_trim_messages` **之前**跑。裁剪是**不可逆**的，
  而摘要是被裁掉那些轮次唯一的底 —— 顺序反过来，就会让"还没进摘要就被删"的内容彻底消失，
  而且不报错、不打日志。这条顺序有两条用例钉着：一条白盒钉调用序，
  一条黑盒断言"一次删掉一大批旧消息时，最早那一轮确实进过摘要输入"。
- **上限要留得住**：`CONVERSATION_MAX_MESSAGES` 必须 ≥
  `(CONVERSATION_MAX_HISTORY_TURNS + CONVERSATION_SUMMARY_TRIGGER_TURNS) × 2`。
  一轮 = 2 条消息，窗口占掉一部分后，留给"待摘要"的余量只有 `cap − window×2`；
  它小于 `trigger×2` 时摘要**一次都攒不够**（`pending` 永远不够长），而裁剪照常删 ——
  这是**静默**失效，所以挡在启动时（`Settings` 的模型校验里），不留给运行期去发现。
  默认值 `100 ≥ (5+10)×2 = 30` 是安全的；但"少存点"是很自然的运维动作，正是这条要拦的。

摘要与长期记忆（Phase 11）都拼在**系统提示词**里，顺序是**摘要 → 记忆**：两者都是"背景"，
但记忆是跨会话的稳定事实、更贴近当前问题，所以放最后。两者都关掉时提示词**逐字**等于从前。

⚠️ 摘要**故意不沿用** `load_history` 的"过滤无依据轮次"：那边是把「知识库中没有找到…」的
**原话**当 assistant 消息逐条喂回去（模型容易顺着那个口径走、越沿用越错），
而摘要是一条**概述**，"问过 X、当时没查到"只是对发生过的事的陈述，诱导强度低得多；
记下"这个话题被问过"本身也有价值（资料补上后再问，模型知道这不是新话题）。
取舍、代价、以及"真要筛该先想清什么"都写在 `_pair_turns` 的 docstring 里。

⚠️ **成本口径**：摘要那一次模型调用发生在**本轮事务内、响应返回之前** ——
也就是"答案已经生成完了，用户还要再等一次摘要生成"。好在它**每 `TRIGGER_TURNS` 轮才发生一次**
（默认 10 轮）、且开关默认关，是偶发成本而非每轮成本。
**流式那条路（第 10 节）已经把它挪到 `done` 之后**（新开 session 补做）；非流式仍如上，
要彻底抹掉得挪进后台任务（需新开 session 并重查会话对象，因为 ORM 实例绑在当前请求的 session 上），
属后续优化，不阻塞现在。

⚠️ 分页：会话详情默认只返回**最新一页**（`limit` + `before_id` keyset 游标 + `has_more`）。
长会话一次全量返回会把每条消息的 `sources` / `meta` 三段 JSON 全吐出去，响应又大又慢。
用 keyset 而不是 offset 的理由和列表页相反：列表翻的是不再变化的存量，
而会话详情翻的是**还在长新消息的尾部**，offset 会在翻页期间漏条/重条。

---

### 10. 流式问答：SSE 的代价是**放弃统一信封**

`POST /api/chat/stream` 与 `/api/chat` **并存**（不是用一个参数去切）—— 和 `/api/agent/chat`
同一个思路：能直接对比，现有调用方与测试也一行不用改。两条路共用 `rag_service._prepare()`，
所以"检索到哪些资料、要不要调模型"的判断**只有一份**，不会出现"非流式答得对、流式答得歪"。

事件协议（前端 `frontend/src/api/stream.js` 按这个解析）：

| 事件 | 载荷 | 时机 |
|---|---|---|
| `status` | `{phase}` | 连接建立、后端刚开工 —— 前端据此显示占位（见下面第 5 条） |
| `step` | `{node, ms, mode, adopted, …}` | **Agent 专属**：每跑完一个节点一条（analyze / retrieve / evaluate / generate） |
| `meta` | `hit` / `sources` / `searched_kb_ids` / `reranked` / `model` / `conversation_id` | 检索完、开始生成**之前** —— 引用卡片先画出来 |
| `delta` | `{text}` | 逐块，可多块（Agent 侧不逐字，没有这个事件） |
| `done` | `answer`（完整）/ `usage` / `finish_reason` / `conversation_id` / `saved` | 结束 |
| `error` | `code` / `message` | **只在"已经开始吐字之后"**才会出现 |

Agent 那条路的事件序列是 `status → step*N → meta → done`；纯 RAG 是 `status → meta → delta*N → done`。
两条共用同一个 `app/api/sse.py` 的帧格式与同一份前端渲染（`Chat.vue` 的 `askStream` 按 `agentMode` 切 URL）——
只有一份实现，才不会漂移。

⚠️ **两条例外**，它们违背"HTTP 恒 200、错误码在 body"这条通用约定：

1. **流内错误不再有 `body.code`** —— 响应头早已发出，状态码改不了，只能用 `event: error` 表达。
2. **鉴权 / 限流 / 越权失败拿到的不是流，是普通信封**（HTTP 200 + `{code: 40100}`）——
   那几件事发生在响应开始**之前**。所以前端**必须按 `content-type` 分流**，不能假定这个接口"总是流"。
   实现上靠一个细节保证：`rag_service.stream_answer()` 是**普通函数**而不是生成器（它先把准备阶段
   跑完再 `return` 内部生成器）。一旦写成 `yield` 开头的生成器，准备阶段就会被推迟到"响应已开始"之后，
   越权就只能变成一个 200 流里的事件 —— 那是降级。

另外几个决策：

- **半截答案不落库**。LLM 中途挂了，已吐出的部分**不写进 `messages`**：半截话术进历史会污染后续的
  喂回（模型会顺着"说到一半"的口径走），和"LLM 失败不留空壳"是同一个口径。前端保留已渲染的正文，
  但会标一行"本次未存入历史"，并靠 `done.saved=false` / `error` 让用户知道 —— 落库失败时答案
  仍完整交付，`saved=false` 就是"这轮没进历史"的唯一通知通道（前端已消费）。
- **落库用另一个 session**。Starlette 对**同步生成器**用线程池迭代，`next()` 落在哪个线程不保证，
  而 SQLAlchemy 的 `Session` 不是线程安全的 —— 所以流式路径自己开 session（`app.db.session.new_session()`）。
  也正因为会话是"落库成功才真正存在"，**新会话的 id 只在 `done` 事件里给**（`meta` 里回显的是客户端
  自己传进来的那个）。
- **用量要显式要（`stream_usage=True`）**。langchain-openai 只在"没传自定义 base_url（或走 LangSmith
  网关）"时才自动开它；本项目传了 `base_url`，不显式传的话请求里没有
  `stream_options={"include_usage": true}`，`done.usage` 恒为 null（tokens 标签消失、meta 里也没用量）。
  该参数只影响流式（`invoke` 不受影响）；DeepSeek 要求 `stream_options` 必须与 `stream:true` 同传，
  langchain 只在流式路径加，不会踩那个 400。
- **摘要与裁剪在 `done` 之后补做**。落库（两条消息 + touch）先提交、先 `yield done` 把答案交付出去；
  摘要那次模型调用与裁剪在**新开的 session** 里补做（`rag_service._maintain_after_stream`），
  失败只 warning —— 摘要字段没动、下一轮重试，裁剪晚一轮只是多留几条消息，失败方向都是"多存"。
  顺序仍是"先摘要、后裁剪"。客户端在 done 一到就断开时这一步会跳过 —— 只是推迟，不丢数据。
  不这么做的代价：非流式时那次模型调用藏在"整段响应"里，流式下就是"最后一个字吐完了、光标还在转"。
- **前端的中止与中断语义**。每次提问持一个 AbortController，"新对话"/删除会话/组件卸载时中止旧流 ——
  不止是观感：旧流的 `done` 回调会把 `conversation_id` 写回 `currentConvId`，把用户刚打开的会话顶掉。
  `done` 写会话 id 前会确认请求未被中止。**流关闭但没等到 `done`**（被反代按空闲超时掐断、网络抖动）
  按中断处理，不会把半截答案当成完整回答。
- **中断分三种状态提示，因为"到底存没存"的结论不同**。① 流内 `error` 事件：后端**明确不落库**，
  可以下"本次未存入历史"这个结论；② 用户主动停止、③ 连接被掐断：落库发生在 `yield done` **之前**，
  与客户端还连不连着无关 → 多半已经写进去了，只能用中性文案，**不能沿用 ① 那句**（会说反）。
  ②③ 还要刷新会话列表 —— 不刷的话库里已经多了个会话、界面上却看不到，得刷新页面才冒出来。
- **`finish_reason` 只有一个用途：`length`**。它意味着答案被 `max_tokens` 截断，用户看到的是半截话。
  所以它**进了 `messages.meta`**（`_turn_meta`）：当场答的与翻历史回放的共用同一个判据，只在当场
  提示一次的话刷新页面就没了，而"这条答案其实是半截的"恰恰是回放时才最容易察觉（当场只当它答完了）。
  其余值前端不渲染。（Agent 路径没有这个字段：它内部多步调用，末步的 finish_reason 无意义。）
- **流断了会自动降级到非流式重取一次**（`Chat.vue` 的 `retryWithoutStream`）。SSE 是长连接，
  放到 nginx 后面被 `proxy_read_timeout` 掐断是常态（本地 Vite 的 120s 只是碰不到）—— 断了之后
  用户手里只有半截答案，而**非流式那条路照样能跑**（同一个后端、同一套检索与生成）。有了它，
  SSE 出问题最多是"慢一点"，不再是"整个问答页废了"。四条边界：
  1. **只重试一次**（不循环，否则一次网络故障会放大成好几次付费调用）；
  2. **用户主动停止不重试**（AbortError 提前分流）；
  3. **降级请求本身也要能中止** —— `chatApi.ask` 的 config 形参就是为传 `signal` 加的，
     否则用户切走会话后那次整段请求还会跑满 180s 再花一次钱；
  4. **降级失败要把原因带出来**：它很可能就是"配额到顶"（走的是同一个 chat 限流器），
     只显示原中断文案会让用户把配额问题当网络问题反复重试。
- 🔴 **"可重试"的判据是 `gotEvent || transport`，别只看前者**。本项目 HTTP 恒 200，
  所以**非 200 按定义就不是我们的信封**，必然是中间层坏了（nginx 502/504）—— 而那正是 SSE
  最常见的死法。若把它并进"信封解析失败"分支，`gotEvent` 会是 false，降级永远等不到
  它要救的场景。（`stream.js` 对 `!resp.ok` 单独抛一个带 `transport: true` 的错误。）
- ⚠️ **降级的代价说清楚**：常见情形是干净的（客户端断开 → 服务端 `GeneratorExit` → 落库之前
  就退出 → 库里没东西，重试只留下一条）。但断流若落在服务端 `db.commit()` 与 `done` 帧送达
  之间那个毫秒级窗口，库里会留下一条**用户从没看到过**的会话，重试又新建一条 →
  侧栏出现两个内容相同的会话，其中一个是隐形历史。低概率、不可消除（落库不看客户端在不在），
  只能这样写明。
- **`status` 事件不是摆设**：它驱动 Agent 侧"正在判定要不要查库…"的占位（文案走 `PHASE_TEXT`
  映射，**不在模板里写死** —— 否则后端新增 phase 时界面会撒谎）。analyze 节点要花一秒上下，
  那期间不显示点什么，用户会以为界面卡死了；第一个 `step` 一到，占位就让位给执行轨迹。
  RAG 侧也发这一帧，为的是把"meta 之前断流"纳入降级范围 —— meta 之后要等上游排队 + 预填充，
  那正是反代 read_timeout 最容易出事的窗口。
- **Agent 侧已同样支持流式**（Phase 14：`POST /api/agent/chat/stream`），但吐的是
  **节点级进度**而不是逐字 —— 这条口径 README 在 P8 就定了：Agent 的价值在
  "多步决策的进度"（判定要不要检索 → 资料够不够 → 要不要换个词再查一轮）而不在
  "一个字一个字地蹦"。真要逐字得给 `generate` 节点注入一条输出通道，复杂度再上一档，
  留给后续。两条流式 SSE 走同一个 `app/api/sse.py` 的帧格式与同一份前端渲染
  （`Chat.vue` 的 `askStream`，按 `agentMode` 切 url）—— 只有一份实现，才不会漂移。
- 🔴 **流式路径里，图必须用新开的 session 装配**。`build_graph(db, user, …)` 是
  **闭包捕获 db** 的（graph.py 有注释解释为什么这么设计），而请求 session 在响应体
  开始迭代**之前**就关了。P13 那次踩的只是"流里碰了一个 ORM 对象"，Agent 这里踩的是
  **整个图的每个节点都在碰 db**（检索读正文、长期记忆读库）—— 一旦抓回请求 session，
  流式这条路会全线炸，而同步路径 `/api/agent/chat` 的用例一条都不会红。
  `tests/test_agent_stream.py` 用 Session 对象身份比对钉着它（变异验证过：改回去就红）。

⚠️ 两个容量/连通性提醒：SSE 是**长连接**，每条连接会占住一个线程直到流结束（Starlette 的线程池默认 40），
单 worker 下并发几十路流就会打满；同时**空闲超过代理的 read_timeout 会被掐断** —— 首 token 之前 `meta`
事件已经先发一帧（生成前就 yield），真正的风险窗口是 meta → 第一个 delta（上游排队 + 预填充），
本地 Vite proxy 配了 120s，放到 nginx（`proxy_read_timeout` 默认 60s）后面就需要**周期心跳**（`: ping`
注释帧）。心跳**刻意没做**：同步生成器阻塞在 `next()` 上时发不出帧，要做得把端点改成 async 生成器 +
线程池迭代 + 定时器竞速，牵动"stream_answer 是普通函数"这个设计点 —— 等真的要部署到反代后面再做。

---

### 11. FAQ 的口径：命中即返回，**不检索也不调模型**

FAQ 是**问答入口之前的一道闸门**，不属于 RAG、也不属于 Agent —— 四个问答端点
（`/chat`、`/chat/stream`、`/agent/chat`、`/agent/chat/stream`）进入各自流程前都先过它。

**为什么单开一层**：同一个制度问题让模型答十次可能十种措辞，而 FAQ 只有一份；
命中即返回既统一了口径，也省掉一次检索和一次生成。**Agent 那条路也过这道闸门** ——
否则用户切到 Agent 模式点同一张卡片，又拿到模型生成的答案，"同一句话两个答案"的问题原样还在。

口径写清楚，免得下次接手的人猜：

| 问题 | 口径 |
|---|---|
| **谁可以配** | 读 = 库的 `read`（普通用户要看得到卡片）；写 = 库的 `admin`。上限 `FAQ_MAX_PER_KB=10`，超了**拒绝并提示**，不静默挤掉旧的 |
| **怎么算命中** | **精确匹配**（去首尾空白后相等），**刻意不做模糊匹配**。用户问"年假能不能延到下一年"被模糊命中到"年假几天"，拿到的是**看着权威、其实答非所问**的东西 —— 比走一趟 RAG 差得多。宁可漏，不可错 |
| **命中后 `sources` 放什么** | **空数组**。答案来自 FAQ 表、不是文档片段，没有可溯源的引用。前端据此关掉引用区、改显示「来自常见问题」标签 |
| **`hit` 是什么** | `true` —— 确实有依据（管理员写的标准答案），只是依据不是文档。真正的"没依据"是 `no_evidence` 那种 |
| **`model` / `usage`** | `null` —— 如实反映没调模型，**不伪造**一个模型名或用量 |
| **与 RAG 口径冲突时以谁为准** | **FAQ 优先**，且这个优先级是**全局**的（不看用户选了哪个模式）。两者不会"各答一半"：命中就是 FAQ 的答案，没命中才轮到 RAG |
| **答案会不会进会话历史** | 会，`meta.answer_mode = "faq"` 一并落库，翻历史时标签照旧显示 |
| **流式端点返回什么** | **统一信封（JSON），不是 SSE**。答案是现成的，没有"逐字生成"这个过程 —— 硬包成 SSE 只是徒增解析成本。⚠️ 这是**成功路径走信封的唯一情况**，所以 `stream.js` 必须先判 `code === 0` 是成功、再当失败处理，顺序反了会把 FAQ 命中弹成"请求失败" |
| **🔴 权限撤销后回放怎么处理** | **已修复（2026-10-15）**：FAQ 命中的 `sources` 是空的，回放脱敏曾因此绕过它。现在答案落库时带 `meta.faq_kb_id`（答案真正出自哪个库），`_cites_revoked_kb` 的判据并入它 —— 撤回授权后历史里的 FAQ 答案与引用答案同口径整条隐藏。⚠️ 判据刻意**不复用** `meta.searched_kb_ids`：那是"检索范围"而不是"答案触及的库"，拿它判会把在三个库里搜、只命中一个的答案误连坐 |

**FAQ 的答案不进向量库、不参与检索**（`test_faq_answer_is_not_vectorized` 钉着）。
它是"命中即返回"的快捷方式，与 RAG 是两条独立的路 —— 混进来会让"这个答案来自哪份文档"
说不清，而**可溯源正是这套系统最值钱的地方**。

---

## 已知限制与后续计划

### 公网作品演示部署

仓库根目录的 `render.yaml` 部署两个入口：轻量静态介绍页由 CDN 直接返回并在后台唤醒服务，
完整 Vue 前端与 FastAPI 由 Web Service 提供，同时自动创建 Render Key Value。
公开演示是固定只读数据，因此用容器内 SQLite 和 JSON 文件向量后端降低免费实例资源占用；
每次实例重建都会执行迁移等价建表、恢复旅游商品/直播场次/知识库与向量数据。访客只能问答和检索，
商品、场次、审批、知识库等写操作由 `DEMO_READ_ONLY` 中间件拒绝。

这只属于作品展示运行方式。本地与企业环境的默认配置仍是 MySQL、Redis、Milvus，数据库结构继续以
Alembic 迁移为唯一权威，向量检索继续使用 Milvus 的知识库范围过滤。

| 项 | 现状 |
|---|---|
| 限流 | 已是**基于 Redis 的跨进程固定窗口**，`--workers N` 下配额也准（维度是登录用户）。降级方向是**放行**：Redis 不可用时不再计数，代价是那段时间额度失控 —— 所以 `/health` 会把 `redis` 单独报出来 |
| 会话记忆 | 两套并存。**会话内**：最近 5 轮进提示词（`CONVERSATION_MAX_HISTORY_TURNS`）。**跨会话长期记忆**已实现（`user_memories` 表 + `/api/memories`），但 **`USER_MEMORY_ENABLED` 默认关** —— 它等于给每次问答多塞一段上下文，得先有开/关两组数据才谈得上值不值。写入是**显式**的（用户主动"记住这件事"）；**没做**"每轮自动提炼"——那要多花一次模型调用，而且会把"刚才在聊什么"这类一次性内容也塞进长期记忆，越攒越脏 |
| 权限撤销与历史 | 已实现**回放脱敏**：答案触发了「此刻已无权访问」的知识库（引用列表 / 文档清单 `catalog_kb_ids` / FAQ `faq_kb_id` 三条判据，2026-10-15 补齐），在会话详情里**整条隐藏**（正文与引用一起换成提示语）。口径是**收回资料、不收回用户自己敲的字** —— 用户自己的提问与会话标题仍然保留。⚠️ 残留：未脱敏的消息仍会回显当时的 `searched_kb_ids`（只有库 id，不含内容） |
| 数据保留 | 已有上限，三个独立的开关（0 = 不限）：会话数 `CONVERSATION_MAX_PER_USER`（超了**拒绝新建并提示**，不静默丢）、单会话消息数 `CONVERSATION_MAX_MESSAGES`（超了按**整轮**删最旧的）、`agent_logs` 保留 `AGENT_LOG_RETENTION_DAYS` 天。**没有调度器**，都在写入时机会式执行（审计清理按小时节流），每次真删都打 WARNING 带条数<br>⚠️ **`audit_logs` 永不清理，刻意不在上面三个开关里** —— 审计是"谁改了权限"的唯一凭据，它的价值随时间**增长**；`agent_logs` 是运行数据，可以过期。**别照它的样子给审计加 prune**，那是最不该有保留策略的一张表 |
| 追问指代 | **做过对照实验，结论是不做 query 改写**。用 8 篇同质文档（全都含"申请/审批/天"）测 14 组省略主语的追问：原话与人工补全的话各跑一次检索（`rerank=true`），**原话 top1 命中 13/14、改写 14/14 —— 只多 1 组，而有效样本（原话判错的组）只有 1/14，低于事前定下的门槛**，所以这个实验**不足以证明改写有价值**（这也是不做的主要理由，不是"改写没用"）。稳定成立的差别在**分数**：原话的 top1 相关分趋近 0（0.0074 ~ 0.24），改写后 0.66 ~ 0.99 —— "名次对了但依据几乎为零"，语料一变大就会失守。结论：当前规模下改写**不改变命中、只提高分数**，不值得为它多付一次模型调用与一次往返延迟；等语料变大、top1 真开始失准再回来做。**8 篇语料、14 组追问、两版完整结果与判定线见 `docs/benchmarks/query-rewrite.md`** |
| 历史压缩 | **已实现摘要压缩**（Phase 12）：滑出「最近 N 轮」窗口的旧轮次会被**增量**压成一段滚动摘要（`conversations.summary` + `summary_upto_id`）并拼进系统提示词，所以长对话里更早聊过的内容不会凭空消失。**默认关**（`CONVERSATION_SUMMARY_ENABLED`）—— 它是一次"多调一次模型换上下文"的买卖，关掉时提示词与从前逐字一致。触发阈值 `CONVERSATION_SUMMARY_TRIGGER_TURNS`(10)、提示词预算 `CONVERSATION_SUMMARY_MAX_CHARS`(800)。⚠️ 它与消息上限有**交叉约束**：`CONVERSATION_MAX_MESSAGES` 必须 ≥ `(CONVERSATION_MAX_HISTORY_TURNS + TRIGGER_TURNS) × 2`，否则摘要永远攒不够输入（一次都不生成）而裁剪照常删 —— 所以启动时就会拒绝这种配置 |
| 相似度阈值 | 未启用 —— 样本不足时设高了会漏掉本该命中的资料（比噪声更糟），先在日志里累积「问题 → 命中分数」分布。⚠️ 注意：光累积**正样本**（库里有答案的问题）定不出这个阈值，需要的是"**明明库里没有**"时分数能到多高的上界 |
| 扫描件 PDF | 不支持（不做 OCR），无文本层时会明确报"可能是扫描件" |
| 流式输出 | **两条问答路都支持 SSE 流式**：纯 RAG 逐字吐（`POST /api/chat/stream`），Agent 吐**节点级进度**（`POST /api/agent/chat/stream`，Phase 14）—— 后者刻意不逐字输出答案，因为它的价值在"多步决策的进度"；真要逐字得给 `generate` 节点注入输出通道，留给后续 |
| 前端测试 | **两条互补的防线，都跟 vitest 无关**：① **纯逻辑**走 `node:test` 零依赖直跑（`npm test` —— 答案性质映射 / 导出 Markdown / 记忆开关 / 趋势图坐标四个模块），**不测组件渲染**；② 渲染与交互的边界靠一个 dev-only 的 SSE 故障模拟来补：`frontend/mock-sse.js`（vite `configureServer` 中间件），问答页顶部有一条虚线工具条（`import.meta.env.DEV` 门控，build 时整条不进产物），或直接开 `/?mock=<模式>`。八种模式：`normal` / `break`（吐 2 帧后断开）/ `http502`（502+HTML，验 transport 判据）/ `error`（流内 error）/ `hang`（首帧前挂 3s）/ `unsaved`（`done.saved=false`）/ `truncated`（`finish_reason='length'`）/ `withconv`（`done.conversation_id` 非 null）。**流式和非流式都走 mock** —— 否则降级重试会打到真后端、真花一次钱。⚠️ 它覆盖不到：真实反代的超时行为、浏览器端的渲染细节（逐字/节流/光标）、以及真 LLM 的 usage 是否回传 |
| 测试基建 | ✅ **已收敛**：所有"自己开 session"的路径（流式落库 / `done` 之后的收尾维护 / 后台文档解析）统一走 `app.db.session.new_session()` —— 测试只需 patch **这一处**。以前是**四处**各存一份工厂（`agent_service` / `rag_service` / `conversation_service` / `document_service`），靠 fixture 的 docstring 提醒"都要换"，而**漏 patch 的后果不是测试变红，而是静默写进真 MySQL**。⚠️ 今后新增"自己开 session"的路径请一律走它，别再引入第二个来源 |
| 登出语义 | 前端「退出」只清除本地 token；JWT 在服务端**不吊销**（后端无状态），仍然有效到过期。⚠️ 但**停用账号、改角色、改密码都能立刻生效** —— 分别靠实时查 `is_active`、实时读角色、以及 token 里的密码指纹（见第 5c 节） |
| 库级成员管理 | 后端支持库级 admin 管成员，但前端「成员」入口目前只给系统管理员（授权需要列全站用户，而 `/api/users` 限系统管理员） |
| Rerank | 已接入。`/chat` 默认开启，`/search` 默认关闭以便对比效果 |

---

## 开源许可

本项目采用 [MIT License](LICENSE)。项目中的公司、商品、账号和业务资料均为模拟数据，
不得将演示流程理解为任何真实平台的官方接口或合作关系。
