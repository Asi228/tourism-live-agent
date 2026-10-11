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
| 会话记忆 · 长期记忆 | **会话内**：问答落 `conversations` / `messages`，最近 N 轮带进提示词 —— 可以追问「那病假呢」，会话列表可翻回历史继续问（详情分页）。**跨会话**：`user_memories` + `/api/memories` 由用户**显式**写入。**长对话**：超出窗口的旧轮次可**增量压缩成摘要**（两个默认关的开关见「已知限制」） |
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
| POST | `/chat/stream` | **RAG 问答（SSE 流式）**：逐字返回，事件 `meta` → `delta`×N → `done`（失败给 `error`）。⚠️ 它**不走统一信封**，且准备阶段的失败仍返回普通信封 —— 见第 10 节 |
| POST | `/agent/chat` | **Agent 问答**：额外返回执行轨迹、检索轮数与答案性质 |
| GET · DELETE | `/conversations` · `/conversations/{id}` | 会话列表（`?limit=` 默认 50、`?offset=` 翻页）/ 详情（默认**最新一页**：`?limit=`、`?before_id=` 往前翻，响应带 `has_more`）/ 删除。**没有"创建"接口** —— 首次提问时隐式建 |
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
| `meta` | `hit` / `sources` / `searched_kb_ids` / `reranked` / `model` / `conversation_id` | 检索完、开始生成**之前** —— 引用卡片先画出来 |
| `delta` | `{text}` | 逐块，可多块 |
| `done` | `answer`（完整）/ `usage` / `finish_reason` / `conversation_id` / `saved` | 结束 |
| `error` | `code` / `message` | **只在"已经开始吐字之后"**才会出现 |

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
  而 SQLAlchemy 的 `Session` 不是线程安全的 —— 所以流式路径自己开 session（`rag_service._session_factory`）。
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
- **Agent 侧仍是非流式**。它的价值在"多步决策的进度"而不是逐字输出，要做应该是节点级事件，
  值得单开一轮；界面上 `mode === 'agent'` 走原路径，两条路的差别一眼可见。

⚠️ 两个容量/连通性提醒：SSE 是**长连接**，每条连接会占住一个线程直到流结束（Starlette 的线程池默认 40），
单 worker 下并发几十路流就会打满；同时**空闲超过代理的 read_timeout 会被掐断** —— 首 token 之前 `meta`
事件已经先发一帧（生成前就 yield），真正的风险窗口是 meta → 第一个 delta（上游排队 + 预填充），
本地 Vite proxy 配了 120s，放到 nginx（`proxy_read_timeout` 默认 60s）后面就需要**周期心跳**（`: ping`
注释帧）。心跳**刻意没做**：同步生成器阻塞在 `next()` 上时发不出帧，要做得把端点改成 async 生成器 +
线程池迭代 + 定时器竞速，牵动"stream_answer 是普通函数"这个设计点 —— 等真的要部署到反代后面再做。

---

## 已知限制与后续计划

| 项 | 现状 |
|---|---|
| 限流 | 已是**基于 Redis 的跨进程固定窗口**，`--workers N` 下配额也准（维度是登录用户）。降级方向是**放行**：Redis 不可用时不再计数，代价是那段时间额度失控 —— 所以 `/health` 会把 `redis` 单独报出来 |
| 会话记忆 | 两套并存。**会话内**：最近 5 轮进提示词（`CONVERSATION_MAX_HISTORY_TURNS`）。**跨会话长期记忆**已实现（`user_memories` 表 + `/api/memories`），但 **`USER_MEMORY_ENABLED` 默认关** —— 它等于给每次问答多塞一段上下文，得先有开/关两组数据才谈得上值不值。写入是**显式**的（用户主动"记住这件事"）；**没做**"每轮自动提炼"——那要多花一次模型调用，而且会把"刚才在聊什么"这类一次性内容也塞进长期记忆，越攒越脏 |
| 权限撤销与历史 | 已实现**回放脱敏**：引用了「此刻已无权访问」的知识库的答案，在会话详情里**整条隐藏**（正文与引用一起换成提示语）。口径是**收回资料、不收回用户自己敲的字** —— 用户自己的提问与会话标题仍然保留。⚠️ 残留：未脱敏的消息仍会回显当时的 `searched_kb_ids`（只有库 id，不含内容） |
| 数据保留 | 已有上限，三个独立的开关（0 = 不限）：会话数 `CONVERSATION_MAX_PER_USER`（超了**拒绝新建并提示**，不静默丢）、单会话消息数 `CONVERSATION_MAX_MESSAGES`（超了按**整轮**删最旧的）、`agent_logs` 保留 `AGENT_LOG_RETENTION_DAYS` 天。**没有调度器**，都在写入时机会式执行（审计清理按小时节流），每次真删都打 WARNING 带条数 |
| 追问指代 | **做过对照实验，结论是不做 query 改写**。用 8 篇同质文档（全都含"申请/审批/天"）测 14 组省略主语的追问：原话与人工补全的话各跑一次检索（`rerank=true`），**原话 top1 命中 13/14、改写 14/14 —— 只多 1 组，而有效样本（原话判错的组）只有 1/14，低于事前定下的门槛**，所以这个实验**不足以证明改写有价值**（这也是不做的主要理由，不是"改写没用"）。稳定成立的差别在**分数**：原话的 top1 相关分趋近 0（0.0074 ~ 0.24），改写后 0.66 ~ 0.99 —— "名次对了但依据几乎为零"，语料一变大就会失守。结论：当前规模下改写**不改变命中、只提高分数**，不值得为它多付一次模型调用与一次往返延迟；等语料变大、top1 真开始失准再回来做。**8 篇语料、14 组追问、两版完整结果与判定线见 `docs/benchmarks/query-rewrite.md`** |
| 历史压缩 | **已实现摘要压缩**（Phase 12）：滑出「最近 N 轮」窗口的旧轮次会被**增量**压成一段滚动摘要（`conversations.summary` + `summary_upto_id`）并拼进系统提示词，所以长对话里更早聊过的内容不会凭空消失。**默认关**（`CONVERSATION_SUMMARY_ENABLED`）—— 它是一次"多调一次模型换上下文"的买卖，关掉时提示词与从前逐字一致。触发阈值 `CONVERSATION_SUMMARY_TRIGGER_TURNS`(10)、提示词预算 `CONVERSATION_SUMMARY_MAX_CHARS`(800)。⚠️ 它与消息上限有**交叉约束**：`CONVERSATION_MAX_MESSAGES` 必须 ≥ `(CONVERSATION_MAX_HISTORY_TURNS + TRIGGER_TURNS) × 2`，否则摘要永远攒不够输入（一次都不生成）而裁剪照常删 —— 所以启动时就会拒绝这种配置 |
| 相似度阈值 | 未启用 —— 样本不足时设高了会漏掉本该命中的资料（比噪声更糟），先在日志里累积「问题 → 命中分数」分布。⚠️ 注意：光累积**正样本**（库里有答案的问题）定不出这个阈值，需要的是"**明明库里没有**"时分数能到多高的上界 |
| 扫描件 PDF | 不支持（不做 OCR），无文本层时会明确报"可能是扫描件" |
| 流式输出 | **纯 RAG 已支持流式**（`POST /api/chat/stream`，SSE，逐字返回，见第 10 节）；**Agent 仍是整段返回** —— 它的价值在"多步决策的进度"，要做应该是节点级事件而不是逐字输出 |
| 登出语义 | 前端「退出」只清除本地 token；JWT 在服务端**不吊销**（后端无状态），仍然有效到过期 |
| 库级成员管理 | 后端支持库级 admin 管成员，但前端「成员」入口目前只给系统管理员（授权需要列全站用户，而 `/api/users` 限系统管理员） |
| Rerank | 已接入。`/chat` 默认开启，`/search` 默认关闭以便对比效果 |
