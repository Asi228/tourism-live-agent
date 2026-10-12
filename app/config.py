from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（app/config.py 往上两级 = enterprise_knowledge_agent/）
BASE_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = BASE_DIR / ".env"

# CHUNK_SIZE 的硬上限（单位：字符）。
# document_chunks.content 是 MySQL TEXT，容量 65535 字节；utf8mb4 下一个汉字占 3~4 字节，
# 留一倍余量取 8000 字符。需求里 chunk 是 500~800 token，正常配置离这个上限很远。
CHUNK_SIZE_HARD_LIMIT = 8000


class Settings(BaseSettings):
    """全部配置集中在这里，从 .env 读取，代码里任何地方都用 settings.xxx。"""

    model_config = SettingsConfigDict(
        # 锚定绝对路径：不管从哪个目录启动服务，都能读到项目根的 .env
        # （用相对路径 ".env" 时，它相对当前工作目录，从上级目录启动会静默读不到）
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        # 未声明的键不报错；由 check_unknown_env_keys() 单独告警，见本文件末尾
        extra="ignore",
    )

    # ===== 应用 =====
    APP_NAME: str = "企业知识库智能 Agent"
    APP_ENV: str = "dev"            # dev / test / prod
    # 只控制日志详细度（True=DEBUG 级别，False=INFO）。
    # 注意 FastAPI 的 debug 页恒为 False，见 app/__init__.py 里的说明：
    # 一旦打开，Starlette 会跳过我们的全局异常处理器，把原始 traceback 返给客户端，
    # 既泄漏 SQL 参数和绝对路径，又绕过日志脱敏。
    DEBUG: bool = True
    API_PREFIX: str = "/api"

    # 启动参数（统一加 APP_ 前缀，避免和系统环境变量 HOST / PORT 撞名）
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8000
    APP_RELOAD: bool = True

    # 跨域白名单，逗号分隔。留空时：dev 放通全部，非 dev 不放通任何来源
    CORS_ORIGINS: str = ""

    # ===== MySQL =====
    MYSQL_HOST: str = "127.0.0.1"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = ""
    MYSQL_DATABASE: str = "enterprise_kb"

    # 连接池 / SQL 日志（独立开关，不跟 DEBUG 绑，避免调试页和刷屏绑在一起）
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    SQL_ECHO: bool = False

    # ===== Redis =====
    REDIS_HOST: str = "127.0.0.1"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = ""
    REDIS_DB: int = 0

    # ===== Milvus =====
    MILVUS_HOST: str = "127.0.0.1"
    MILVUS_PORT: int = 19530
    # collection 名故意与 MySQL 表 document_chunks 同名：
    # 两边存的是同一种东西（切分块），名字一致对应关系一眼看得出。
    MILVUS_COLLECTION: str = "document_chunks"
    MILVUS_TIMEOUT: float = 10.0

    # ===== JWT =====
    JWT_SECRET: str = "change-me"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 1440

    # ===== 初始管理员 =====
    # 注册接口默认给 employee 角色，为了让系统里能出现第一个管理员，
    # init_db() 会按这两项创建一个 admin 账号。留空则跳过（并记 warning）。
    ADMIN_USERNAME: str = ""
    ADMIN_PASSWORD: str = ""

    # ===== 开放注册 =====
    # **默认关**：企业内部的账号应当由管理员开通（`POST /api/users`），
    # 而不是任何人打开注册页就能给自己造一个。开着的时候它等于一条
    # "谁能进这个系统"的公开入口，而且会被人用来占用户名 / 邮箱。
    # 需要自助注册的场景（比如内网试用）可以打开，但要知道自己开了什么。
    AUTH_ALLOW_REGISTER: bool = False

    # ===== 文档上传 =====
    # 落盘根目录。每个知识库一个子目录（uploads/<kb_id>/），
    # 磁盘上的文件名由 uuid 生成，**绝不使用用户上传的文件名**（防路径穿越/重名覆盖）。
    UPLOAD_DIR: str = str(BASE_DIR / "uploads")
    MAX_UPLOAD_SIZE_MB: int = 20
    ALLOWED_EXTENSIONS: str = "pdf,txt,md,markdown"

    # ===== 文本切分 =====
    # 单位是**字符**而不是 token。原因：BGE-M3 走 API，本地不装 tokenizer
    # （transformers + torch 是几百 MB，本机 16G 内存 + 核显扛不住）。
    # 中文场景 1 个字符 ≤ 1 个 token，按字符切是偏保守（不会超长）的近似。
    # 将来要精确到 token，只改 app/rag/splitter.py 一个文件即可。
    CHUNK_SIZE: int = 600
    CHUNK_OVERLAP: int = 100

    # ===== LLM =====
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = "https://api.deepseek.com"
    # ⚠️ 这个模型**默认开着思考模式**：实测同一个问题的 completion_tokens
    # 从 6 涨到 36（121 字的思考过程也算 completion token）。
    # RAG 的上下文比单句长得多，思考只会更长，所以调用时**必须显式关掉**，
    # 见 app/rag/llm.py 里的 THINKING_DISABLED。
    LLM_MODEL: str = "deepseek-flash"
    LLM_TIMEOUT: float = 60.0
    # 生成答案的长度上限。RAG 答案通常几百字，2048 足够；
    # 这只是上限，正常不会用满（服务端允许的上限远大于此，无需调大）。
    LLM_MAX_TOKENS: int = 2048
    # 0.3：知识库问答要的是"照资料说"，不是创作，低温度更稳。
    LLM_TEMPERATURE: float = 0.3

    # ===== RAG 问答 =====
    # 送进 Prompt 的检索条数。条数越多上下文越长越贵，5 条是常见折中。
    RAG_TOP_K: int = 5
    # 拼进 Prompt 的上下文**字符**上限（和 CHUNK_SIZE 一样用字符近似 token）。
    # 超出时从**分数最低**的结果开始丢弃，保证高分内容一定进得去。
    RAG_MAX_CONTEXT_CHARS: int = 6000
    # 引用列表里每条展示的正文摘要长度
    RAG_SNIPPET_CHARS: int = 120

    # ===== Rerank 重排（Phase 7）=====
    # 开关：rerank 只是「让排序更准」，不是必需品，所以给一个能一键退回纯向量检索的开关。
    RAG_RERANK_ENABLED: bool = True
    # 初筛候选数：向量检索先多召回一些（保**召回率**），再交给 reranker 精排（保**精度**）。
    # 实测 100 条候选一次调用约 1.7 秒，20 条很轻松。必须 >= RAG_TOP_K，否则精排没意义。
    RAG_CANDIDATE_K: int = 20
    # 实测可用（2026-10-04）：同服务商下还有 Pro/BAAI/bge-reranker-v2-m3 与 Qwen3-Reranker 系列
    RERANK_MODEL: str = "BAAI/bge-reranker-v2-m3"
    RERANK_TIMEOUT: float = 30.0

    # ===== 限流（Redis 跨进程固定窗口）=====
    # 限流保护的是"钱"：一次 /api/chat 要打 rerank + LLM 两个付费上游，
    # 而 Phase 8 的 Agent 会自己决定调几次工具 —— 所以它不该等到安全加固阶段才做。
    # 计数落在 Redis（连接参数见上面的 REDIS_*），`--workers N` 下也准；
    # ⚠️ Redis 不可用时**放行**（降级），代价见 app/core/rate_limit_redis.py 的文件头。
    RATE_LIMIT_ENABLED: bool = True
    # 每个用户每分钟的配额，**0 表示该类型不限流**
    RATE_LIMIT_CHAT_PER_MINUTE: int = 20       # 最贵，掐最紧
    RATE_LIMIT_SEARCH_PER_MINUTE: int = 60
    RATE_LIMIT_VECTORIZE_PER_MINUTE: int = 5   # 一次可能打几百条 embedding

    # ===== Agent（Phase 8）=====
    # 开关：Agent 一次问答要多调 2~4 次大模型，出问题能一键退回纯 RAG 的 /api/chat
    AGENT_ENABLED: bool = True
    # 最多检索几轮。这是**成本硬闸门**：要不要再检索一次是模型自己决定的，
    # 没有上限就等于把花费的控制权完全交给它。实测 2 轮已足够覆盖
    # "换个关键词再找一次"这种最常见的二次尝试。
    AGENT_MAX_ITERATIONS: int = 2

    # ===== 会话记忆（Phase 9）=====
    # 带进 Prompt 的历史轮数。1 轮 = 一问一答 = 2 条消息；**0 表示不带历史**
    # （退化成 Phase 8 的单轮行为，用来对比"多轮到底有没有用"）。
    #
    # 别调太大：历史是按字面拼进 Prompt 的，token 随轮数**线性上涨**；
    # 而且越旧的轮次相关性越差 —— 真正该占上下文的是本轮检索到的资料。
    # 历史过长时"摘要压缩"是正解（把远期轮次压成一段话），但那是后续的事，
    # 本轮先用"只带最近 N 轮"这个最简单、也最容易解释清楚的做法。
    CONVERSATION_MAX_HISTORY_TURNS: int = 5

    # ===== 长对话摘要压缩（Phase 12）=====
    # 上面那个开关只带"最近 N 轮"，更早的**直接丢**。打开这一组之后，滑出窗口的旧轮次会被
    # 增量压成一段滚动摘要，拼进系统提示词 —— 模型仍然知道"前面聊过什么"。
    #
    # **默认关**：一次压缩要多调一次模型（是"多花钱换上下文"的买卖），
    # 和 RAG_RERANK_ENABLED / USER_MEMORY_ENABLED 同一个习惯：先有开关两组数据再谈值不值。
    # 关掉时行为与上一阶段**逐字一致**（不生成、也不注入）。
    CONVERSATION_SUMMARY_ENABLED: bool = False
    # 攒够多少轮"已滑出窗口且还没被摘要"的对话才压缩一次。
    # 设太小会频繁调模型；设太大则摘要覆盖面偏旧（中间那段既不在窗口里、也没进摘要）。
    CONVERSATION_SUMMARY_TRIGGER_TURNS: int = 10
    # 摘要拼进提示词的字符上限（超出**从头截断**，保留更近的部分）。
    CONVERSATION_SUMMARY_MAX_CHARS: int = 800

    # ===== 数据保留（Phase 10）=====
    # 上限分三类，**每个都支持 0 = 不限**（关掉某一项不影响其它项）。
    # 口径：这三条治的是"只增不减"，而不是"喂给模型多少" —— 后者是上面那个
    # CONVERSATION_MAX_HISTORY_TURNS 管的（那个只影响 Prompt，不动库里数据）。
    #
    # ⚠️ 后两条是**不可逆**的删除，只在写入时机会式执行（没有调度器），
    #    每次真删都会打 WARNING 带条数，便于事后追溯。
    #
    # 每用户最多保留多少个会话。超了**拒绝新建并提示去删旧会话**，不静默丢数据。
    CONVERSATION_MAX_PER_USER: int = 200
    # 每个会话最多保留多少条消息（**按整轮删最旧的**：一轮 = 一问一答 = 2 条，
    # 只删到整轮边界，不留下"没有对应问题的孤立答案"）。
    CONVERSATION_MAX_MESSAGES: int = 100
    # agent_logs 保留天数（纯排障数据，只写不清会一直涨）。
    AGENT_LOG_RETENTION_DAYS: int = 30

    # ===== 长期记忆（Phase 11）=====
    # 跨会话记住"关于用户的稳定事实"（"我在财务部""报销截止是每月 25 号"）。
    # **默认关**：它等于给每次问答多塞一段上下文，得先有开/关两组数据才谈得上值不值 ——
    # 和 RAG_RERANK_ENABLED、CONVERSATION_MAX_HISTORY_TURNS=0 是同一个习惯。
    # 关掉时行为与上一阶段**逐字一致**（提示词不多一个字符）。
    USER_MEMORY_ENABLED: bool = False
    # 存量上限：每用户最多存多少条（**0 = 不限**）。超了**拒绝写入并提示**，
    # 不静默删最旧的 —— 静默删用户数据是数据损坏，让他自己删只是麻烦。
    # ⚠️ 这一项管的是**库里存多少**（不可逆），与下面两项"送进提示词多少"是两回事。
    USER_MEMORY_MAX_PER_USER: int = 200
    # 送进提示词最多几条（按最近更新倒序；**0 = 不带记忆**）。
    # ⚠️ 取的是**最近更新的前 N 条**，**不是"最相关的 N 条"** —— 不查向量、不做相关性排序。
    #    这是刻意的：可预测、每次问答不多花一次检索；代价是第 N+1 条之后即使更相关也进不去。
    #    真需要按相关性选，那是另一件事（记忆也要建索引 + 检索），别在这里顺手改。
    USER_MEMORY_MAX_ITEMS: int = 20
    # 记忆段的字符预算（超出时**从最旧的开始丢**），防止它把本轮检索到的资料挤出上下文。
    USER_MEMORY_MAX_CHARS: int = 1000

    # ===== Embedding =====
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_BASE_URL: str = ""
    EMBEDDING_MODEL: str = "BAAI/bge-m3"
    EMBEDDING_DIM: int = 1024
    # 每次请求送多少条文本去向量化。
    # 实测（硅基流动 BGE-M3）：单次 200 条仍正常，且**延迟不随条数线性增长**
    # （100 条一批约 2 秒，逐条送则约 100 秒，差 50 倍）。
    # 默认 64 是折中：批量收益已基本饱和，而单次失败要重试的数据量还不大。
    EMBEDDING_BATCH_SIZE: int = 64

    @model_validator(mode="after")
    def _check_document_settings(self) -> "Settings":
        """切分与上传参数在启动时就校验，不留到处理文档时才炸。

        为什么这里是抛错而不是像 check_unknown_env_keys() 那样只告警：
        那一项拼错只是回退默认值，系统照常能用；而 CHUNK_OVERLAP >= CHUNK_SIZE
        会让 splitter 每份文档都抛 ValueError，所有文档永远停在 failed ——
        这种"配置错了整个功能就废"的情况必须当场拦住。
        """
        if self.CHUNK_SIZE <= 0:
            raise ValueError("CHUNK_SIZE 必须大于 0")
        if self.CHUNK_SIZE > CHUNK_SIZE_HARD_LIMIT:
            raise ValueError(
                f"CHUNK_SIZE 不能超过 {CHUNK_SIZE_HARD_LIMIT} 字符："
                "document_chunks.content 是 MySQL TEXT（65535 字节），"
                "utf8mb4 下 4 字节一个汉字，再大就会截断报错"
            )
        if self.CHUNK_OVERLAP < 0:
            raise ValueError("CHUNK_OVERLAP 不能为负数")
        if self.CHUNK_OVERLAP >= self.CHUNK_SIZE:
            raise ValueError("CHUNK_OVERLAP 必须小于 CHUNK_SIZE，否则切分会不收敛")
        if self.MAX_UPLOAD_SIZE_MB <= 0:
            raise ValueError("MAX_UPLOAD_SIZE_MB 必须大于 0")
        if not self.allowed_extensions_set:
            raise ValueError("ALLOWED_EXTENSIONS 不能为空，否则任何文件都传不上来")
        return self

    @model_validator(mode="after")
    def _check_rag_settings(self) -> "Settings":
        """RAG 参数校验。

        为什么 RAG_MAX_CONTEXT_CHARS 必须有下限：它配得太小时，可能连一条检索结果的
        「[1] 来源：某文件」标题都放不进去 → 拼出来是空上下文，而 answer() 就会拿着
        **空资料**去调大模型（白花钱，也违背"答案必须来自文档"的承诺）。
        rag_service 里已经有运行时守卫，但能在启动时拦住的配置错误，不该留给运行时。
        500 字符 ≈ 一个来源标题 + 一小段正文，是"至少能答出一句"的下限。
        """
        if self.RAG_MAX_CONTEXT_CHARS < 500:
            raise ValueError(
                "RAG_MAX_CONTEXT_CHARS 不能小于 500 字符：太小时检索结果一条都拼不进上下文，"
                "问答会退化成「没有资料却仍去问大模型」"
            )
        if self.RAG_TOP_K <= 0:
            raise ValueError("RAG_TOP_K 必须大于 0")
        if self.RAG_SNIPPET_CHARS <= 0:
            raise ValueError("RAG_SNIPPET_CHARS 必须大于 0")
        if self.RAG_CANDIDATE_K <= 0:
            raise ValueError("RAG_CANDIDATE_K 必须大于 0")
        if self.CONVERSATION_MAX_HISTORY_TURNS < 0:
            raise ValueError(
                "CONVERSATION_MAX_HISTORY_TURNS 不能为负数（0 表示不带历史，是合法值）"
            )
        if self.CONVERSATION_SUMMARY_TRIGGER_TURNS < 1:
            raise ValueError(
                "CONVERSATION_SUMMARY_TRIGGER_TURNS 至少为 1（0 会让每次追加都触发压缩）"
            )
        if self.CONVERSATION_SUMMARY_MAX_CHARS < 100:
            raise ValueError(
                "CONVERSATION_SUMMARY_MAX_CHARS 不能小于 100：太短的摘要段没有意义，"
                "等于白花一次模型调用"
            )
        if self.CONVERSATION_MAX_PER_USER < 0:
            raise ValueError("CONVERSATION_MAX_PER_USER 不能为负数（0 表示不限）")
        if self.CONVERSATION_MAX_MESSAGES < 0:
            raise ValueError("CONVERSATION_MAX_MESSAGES 不能为负数（0 表示不限）")
        if (
            self.CONVERSATION_SUMMARY_ENABLED
            and self.CONVERSATION_MAX_HISTORY_TURNS > 0
            and self.CONVERSATION_MAX_MESSAGES > 0
        ):
            # 摘要是"被裁掉的旧轮次唯一的底"，所以它必须**追得上裁剪**。
            # 一轮 = 2 条消息；摘只在"窗口外、且还没被摘要覆盖"的轮次攒够
            # CONVERSATION_SUMMARY_TRIGGER_TURNS 轮时才动手，而库里最多只留
            # CONVERSATION_MAX_MESSAGES 条 —— 窗口占掉一部分之后，留给"待摘要"
            # 的余量只有 (cap - window*2)。它若小于 trigger*2，pending 永远攒不够，
            # 摘要**一次都不会发生**，而裁剪照常删 —— 旧轮次被静默丢弃。
            # （默认值 100 ≥ (5+10)*2=30 是安全的，但"少存点"是很自然的运维动作。）
            need = (
                self.CONVERSATION_MAX_HISTORY_TURNS
                + self.CONVERSATION_SUMMARY_TRIGGER_TURNS
            ) * 2
            if self.CONVERSATION_MAX_MESSAGES < need:
                raise ValueError(
                    "开启对话摘要时，CONVERSATION_MAX_MESSAGES 必须不小于 "
                    "(CONVERSATION_MAX_HISTORY_TURNS + CONVERSATION_SUMMARY_TRIGGER_TURNS) * 2："
                    f"当前 {self.CONVERSATION_MAX_MESSAGES} < {need}，"
                    "摘要会永远攒不够输入（一次都不生成），而裁剪照常执行 —— "
                    "结果是最旧的轮次被删掉且从未被摘要过。"
                    "要么调大上限，要么关掉摘要（CONVERSATION_SUMMARY_ENABLED=false）"
                )
        if self.AGENT_LOG_RETENTION_DAYS < 0:
            raise ValueError("AGENT_LOG_RETENTION_DAYS 不能为负数（0 表示不限）")
        if self.USER_MEMORY_MAX_PER_USER < 0:
            raise ValueError("USER_MEMORY_MAX_PER_USER 不能为负数（0 表示不限）")
        if self.USER_MEMORY_MAX_ITEMS < 0:
            raise ValueError("USER_MEMORY_MAX_ITEMS 不能为负数（0 表示不带记忆）")
        if self.USER_MEMORY_MAX_CHARS < 0:
            raise ValueError("USER_MEMORY_MAX_CHARS 不能为负数（0 表示不带记忆）")
        if self.AGENT_MAX_ITERATIONS < 1:
            raise ValueError("AGENT_MAX_ITERATIONS 必须至少为 1（否则永远不会检索）")
        if self.RAG_RERANK_ENABLED and self.RAG_CANDIDATE_K < self.RAG_TOP_K:
            raise ValueError(
                "开启重排时 RAG_CANDIDATE_K 必须不小于 RAG_TOP_K："
                f"当前候选 {self.RAG_CANDIDATE_K} < 最终要 {self.RAG_TOP_K} 条，"
                "精排就没有挑选余地了（重排的意义就是「多召回、再精选」）"
            )
        return self

    @property
    def mysql_url(self) -> str:
        """SQLAlchemy 连接串。

        user / password / database 必须 URL 编码（quote_plus）：
        密码里只要出现 @ : / # ? 之一，不编码就会把连接串解析错位
        （例如密码 p@ss/w#rd 会让 host 变成 "ss"）。
        """
        return (
            f"mysql+pymysql://{quote_plus(self.MYSQL_USER)}:{quote_plus(self.MYSQL_PASSWORD)}"
            f"@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{quote_plus(self.MYSQL_DATABASE)}"
            f"?charset=utf8mb4"
        )

    @property
    def redis_url(self) -> str:
        auth = f":{quote_plus(self.REDIS_PASSWORD)}@" if self.REDIS_PASSWORD else ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def cors_origins_list(self) -> list[str]:
        """把逗号分隔的 CORS_ORIGINS 拆成列表。"""
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def upload_path(self) -> Path:
        """上传目录的绝对路径。

        相对路径一律按**项目根**解析，而不是当前工作目录 ——
        和 ENV_FILE 同一个理由：从别的目录启动服务时，CWD 相对路径会把文件写飞。
        """
        path = Path(self.UPLOAD_DIR)
        return path if path.is_absolute() else BASE_DIR / path

    @property
    def allowed_extensions_set(self) -> set[str]:
        """允许的扩展名（统一小写、不带点）。"""
        return {
            e.strip().lower().lstrip(".")
            for e in self.ALLOWED_EXTENSIONS.split(",")
            if e.strip()
        }


@lru_cache
def get_settings() -> Settings:
    """单例，全局复用，避免反复读 .env。"""
    return Settings()


def _read_env_keys(env_path: Path) -> list[str]:
    """纯文本解析 .env，取出所有键名（转大写）。

    不依赖 pydantic，方便单独测试。
    """
    if not env_path.exists():
        return []
    keys: list[str] = []
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip().upper()
        if key:
            keys.append(key)
    return keys


def check_unknown_env_keys() -> list[str]:
    """.env 里写了、但 Settings 未声明的键 —— 基本就是拼写错误。

    返回列表而不抛异常：一行拼错的配置不应该让整个服务起不来，
    但要能在启动日志里被看见（否则会静默回退默认值，很难排查）。
    """
    declared = {name.upper() for name in Settings.model_fields}
    return [key for key in _read_env_keys(ENV_FILE) if key not in declared]


settings = get_settings()
