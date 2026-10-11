# 企业知识库智能 Agent

面向企业内部文档的 RAG 问答系统：把公司制度、流程类文档灌进知识库，
员工用自然语言提问，**答案必须来自公司文档，并带着可点击核对的来源引用**。

除了常规的「检索 → 生成」问答，还实现了一个 **LangGraph 状态机 Agent**：
它会先判断"这个问题要不要查库"，检索后再评估"资料够不够"，
不够就换个关键词再查一次 —— 并把每一步的决策与耗时完整返回出来。

---

## 功能

| 模块 | 能力 |
|---|---|
| 认证与权限 | JWT 登录注册；角色（admin / employee）；知识库级授权（read / write / admin 三档） |
| 知识库 | 创建、改名、公开/私有；成员授权与移除 |
| 文档 | 上传（PDF / TXT / MD）→ 解析 → 清洗 → 切分 → 落库；后台状态流转（pending → processing → completed / failed） |
| 向量化 | BGE-M3 批量向量化写入 Milvus；支持按文档或整库批量灌数据 |
| 检索 | **两段式**：向量多召回 → BGE-Reranker 精排；**权限过滤在检索阶段完成** |
| RAG 问答 | 检索 → 拼提示词（资料带 `[1][2]` 编号）→ 生成 → 返回答案 + 引用（文件名 / 页码 / 相似度 / 摘要） |
| Agent 问答 | 状态机多步决策（要不要查 → 查 → 够不够 → 换词再查 → 生成），返回每步节点、耗时与决策 |
| 会话记忆 · 长期记忆 | **会话内**：问答落 `conversations` / `messages`，最近 N 轮带进提示词 —— 可以追问「那病假呢」，会话列表可翻回历史继续问。**跨会话**：`user_memories` + `/api/memories` 由用户**显式**写入（默认关，见 `USER_MEMORY_ENABLED`） |
| 可观测性 | 执行轨迹落库（`agent_logs`）、token 统计、向量自检（孤儿向量、「已解析但未向量化」的文档清单） |
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

**测试**：

```bash
pytest -q                                          # 单元测试（不打任何真实外部接口）

# 集成测试默认全部跳过（它们依赖 VM 上的 Milvus / Redis、或本机 MySQL，放进主测试集
# 会因为"服务没开机"变成一片红，别人 clone 下来第一件事就是测试全挂）。三个开关分别打开：
RUN_MILVUS_INTEGRATION=1 pytest tests/test_vectors_integration.py -v    # 连真实 Milvus
RUN_REDIS_INTEGRATION=1  pytest tests/test_limiter_integration.py -v    # 连真实 Redis
RUN_MYSQL_INTEGRATION=1  pytest tests/test_migrations.py -v             # 迁移 drift 检查（连真实 MySQL）

# 一起开（需要两台 VM 都在跑）
RUN_MILVUS_INTEGRATION=1 RUN_REDIS_INTEGRATION=1 RUN_MYSQL_INTEGRATION=1 pytest -v
```

---

## 接口一览

所有接口前缀 `/api`，统一响应体 `{"code": 0, "message": "success", "data": ...}`
（HTTP 状态码恒为 200，真实业务错误码在 body 里），鉴权用 `Authorization: Bearer <token>`。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/auth/register` · `/auth/login` · GET `/auth/me` | 注册 / 登录 / 当前用户 |
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
| POST | `/agent/chat` | **Agent 问答**：额外返回执行轨迹、检索轮数与答案性质 |
| GET · DELETE | `/conversations` · `/conversations/{id}` | 会话列表（`?limit=` 默认 50、`?offset=` 翻页）/ 详情（含全部消息）/ 删除。**没有"创建"接口** —— 首次提问时隐式建 |
| GET · POST · DELETE | `/memories` · `/memories/{id}` | **长期记忆**：跨会话记住的事实（列表 / 写入 / 删除）。归属校验同会话：别人的 id 一律 404 |
| GET | `/vector/health` · POST `/vector/purge-orphans` | 向量自检 / 清理孤儿向量（管理员） |
| GET | `/health` | 健康检查（MySQL / Milvus / Redis），逐项 `ok/fail`；3 秒内复用结果 |

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

### 3. Agent 用状态机，是因为"要不要再检索一次"取决于上一轮的结果

这是顺序调用表达不了的东西：它需要**条件分支 + 回到上一步的循环 + 跨轮累积的状态**。
LangGraph 把这三样变成显式的东西 —— 图长什么样，决策逻辑就长什么样，可以被画出来、被测试、被讲清楚。

同时设了**三道成本闸门**（Agent 比纯 RAG 多调几次模型）：

1. `AGENT_MAX_ITERATIONS` —— 最多检索几轮，到顶强制生成；
2. 判定节点只用 `max_tokens=8` —— 只要一个词，不给它长篇大论的机会；
3. `recursion_limit` —— LangGraph 自带的兜底。

另外两条降级方向是**刻意相反**的：判定节点（analyze）失败 → 默认**去检索**（宁可多花钱也不凭空答）；
评估节点（evaluate）失败 → 默认**收手**（不因为解析失败反复烧钱）。

### 4. Milvus 只存向量与定位字段，正文留在 MySQL

collection 里只有 `chunk_id`（主键）/ `document_id` / `knowledge_base_id` / `page_number` / `vector`。
用 MySQL 的 chunk_id 当主键还有个好处：**写入天然幂等**，重复向量化不会产生重复数据。
检索命中后回 MySQL 批量取正文与文件名（一条 SQL，不是 N+1）。

### 5. 审计与业务解耦

`agent_logs` 记录每次 Agent 运行的轨迹、token 与耗时。它由 service 层单独提交 ——
写日志失败会被吞掉并返回 `None`，**绝不让用户拿不到已经生成好的答案**。

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
│   ├── rag/                # 解析、清洗、切分、向量化、重排、提示词、模型客户端
│   ├── agent/              # LangGraph 状态机（state / tools / graph）
│   ├── db/                 # Engine、Session、建表与初始化
│   ├── core/               # 配置、异常与错误码、统一响应、日志、限流
│   └── utils/              # 文件落盘与校验
├── frontend/               # 正式前端（Vue3 + Vite + Element Plus）
│   ├── src/api/            #   接口封装（路径只在这里出现一次）
│   ├── src/stores/         #   Pinia（登录态）
│   ├── src/router/         #   路由 + 登录守卫
│   ├── src/views/          #   登录 / 知识库 / 文档 / 智能问答
│   └── src/layouts/        #   侧边栏 + 顶栏
├── devtools/index.html     # 单文件后端调试台（开发自测）
├── docs/                   # 设计与实验记录（benchmarks/ 存对照实验的语料、数据与结论）
├── tests/                  # 单元测试 + 可选的集成测试（Milvus / Redis / MySQL）
├── migrations/             # Alembic 迁移脚本（全新环境也要靠它，见下）
├── alembic.ini             # Alembic 配置（连接串不写这里，由 migrations/env.py 从 .env 读）
├── pytest.ini              # pytest 配置
├── .env.example            # 配置模板（只放占位符）
└── requirements.txt
```

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

## 已知限制与后续计划

| 项 | 现状 |
|---|---|
| 限流 | 已是**基于 Redis 的跨进程固定窗口**，`--workers N` 下配额也准（维度是登录用户）。降级方向是**放行**：Redis 不可用时不再计数，代价是那段时间额度失控 —— 所以 `/health` 会把 `redis` 单独报出来 |
| 会话记忆 | 两套并存。**会话内**：最近 5 轮进提示词（`CONVERSATION_MAX_HISTORY_TURNS`）。**跨会话长期记忆**已实现（`user_memories` 表 + `/api/memories`），但 **`USER_MEMORY_ENABLED` 默认关** —— 它等于给每次问答多塞一段上下文，得先有开/关两组数据才谈得上值不值。写入是**显式**的（用户主动"记住这件事"）；**没做**"每轮自动提炼"——那要多花一次模型调用，而且会把"刚才在聊什么"这类一次性内容也塞进长期记忆，越攒越脏 |
| 权限撤销与历史 | 已实现**回放脱敏**：引用了「此刻已无权访问」的知识库的答案，在会话详情里**整条隐藏**（正文与引用一起换成提示语）。口径是**收回资料、不收回用户自己敲的字** —— 用户自己的提问与会话标题仍然保留。⚠️ 残留：未脱敏的消息仍会回显当时的 `searched_kb_ids`（只有库 id，不含内容） |
| 数据保留 | 已有上限，三个独立的开关（0 = 不限）：会话数 `CONVERSATION_MAX_PER_USER`（超了**拒绝新建并提示**，不静默丢）、单会话消息数 `CONVERSATION_MAX_MESSAGES`（超了按**整轮**删最旧的）、`agent_logs` 保留 `AGENT_LOG_RETENTION_DAYS` 天。**没有调度器**，都在写入时机会式执行（审计清理按小时节流），每次真删都打 WARNING 带条数 |
| 追问指代 | **做过对照实验，结论是不做 query 改写**。用 8 篇同质文档（全都含"申请/审批/天"）测 14 组省略主语的追问：原话与人工补全的话各跑一次检索（`rerank=true`），**原话 top1 命中 13/14、改写 14/14 —— 只多 1 组，而有效样本（原话判错的组）只有 1/14，低于事前定下的门槛**，所以这个实验**不足以证明改写有价值**（这也是不做的主要理由，不是"改写没用"）。稳定成立的差别在**分数**：原话的 top1 相关分趋近 0（0.0074 ~ 0.24），改写后 0.66 ~ 0.99 —— "名次对了但依据几乎为零"，语料一变大就会失守。结论：当前规模下改写**不改变命中、只提高分数**，不值得为它多付一次模型调用与一次往返延迟；等语料变大、top1 真开始失准再回来做。**8 篇语料、14 组追问、两版完整结果与判定线见 `docs/benchmarks/query-rewrite.md`** |
| 历史压缩 | 只截最近 N 轮，没有摘要压缩。长对话里更早的内容会被直接丢掉 |
| 相似度阈值 | 未启用 —— 样本不足时设高了会漏掉本该命中的资料（比噪声更糟），先在日志里累积「问题 → 命中分数」分布。⚠️ 注意：光累积**正样本**（库里有答案的问题）定不出这个阈值，需要的是"**明明库里没有**"时分数能到多高的上界 |
| 扫描件 PDF | 不支持（不做 OCR），无文本层时会明确报"可能是扫描件" |
| 流式输出 | 问答接口是**非流式**的（一问一答返回整段）；改流式只需要前端一个页面加 SSE 处理 |
| 登出语义 | 前端「退出」只清除本地 token；JWT 在服务端**不吊销**（后端无状态），仍然有效到过期 |
| 库级成员管理 | 后端支持库级 admin 管成员，但前端「成员」入口目前只给系统管理员（授权需要列全站用户，而 `/api/users` 限系统管理员） |
| Rerank | 已接入。`/chat` 默认开启，`/search` 默认关闭以便对比效果 |
