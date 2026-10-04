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
| 前端 | 后端调试台（单文件 HTML，开发自测用）；正式前端 Vue3 + Vite + Element Plus（规划中） |

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
python main.py                         # 或 uvicorn main:app --reload
```

首次启动会**自动建表**、初始化角色与 `.env` 中配置的初始管理员（幂等，重复启动不会覆盖已有密码）。

- 接口文档：`http://127.0.0.1:8000/docs`
- 开发调试台：`http://127.0.0.1:8000/devtools/`（登录 / 知识库 / 文档 / 向量检索 / 问答 / Agent 轨迹）

**测试**：

```bash
pytest -q                                          # 单元测试（不打任何真实外部接口）
RUN_MILVUS_INTEGRATION=1 pytest tests/test_vectors_integration.py -v   # 连真实 Milvus 的集成测试
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
| POST | `/chat` | **RAG 问答**：返回答案 + 引用来源 |
| POST | `/agent/chat` | **Agent 问答**：额外返回执行轨迹、检索轮数与答案性质 |
| GET | `/vector/health` · POST `/vector/purge-orphans` | 向量自检 / 清理孤儿向量（管理员） |
| GET | `/health` | 健康检查（MySQL / Milvus / Redis） |

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
├── devtools/index.html     # 单文件调试台（开发自测）
├── tests/                  # 单元测试 + 可选的真实 Milvus 集成测试
├── .env.example            # 配置模板（只放占位符）
└── requirements.txt
```

---

## 已知限制与后续计划

| 项 | 现状 |
|---|---|
| 限流 | 当前是**进程内**固定窗口实现，单进程下准确；多 worker 部署时会各自计数（已替换点标注在代码与配置中） |
| 会话与记忆 | 尚未实现多轮对话与用户长期记忆，问答目前是**无状态**的 |
| 相似度阈值 | 未启用 —— 样本不足时设高了会漏掉本该命中的资料（比噪声更糟），先在日志里累积「问题 → 命中分数」分布 |
| 扫描件 PDF | 不支持（不做 OCR），无文本层时会明确报"可能是扫描件" |
| 正式前端 | 当前只有后端调试台；Vue3 前端待做 |
| Rerank | 已接入。`/chat` 默认开启，`/search` 默认关闭以便对比效果 |
