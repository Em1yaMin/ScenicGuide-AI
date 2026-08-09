import os
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, model_validator


_env_path = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_env_path, override=True)


# 默认 CORS 白名单（批次6 C3 安全加固）：演示环境为本机宿主机访问
# （192.168.16.132），前端 5173 / ADH 前端 3000 / ADH 后端 8001 跨端口调用；
# 默认同时覆盖 localhost / 127.0.0.1 / 本机 IP 各端口，生产环境应显式收紧
# 为实际部署域名（CORS_ORIGINS 环境变量，逗号分隔）。
_DEFAULT_CORS_ORIGINS = [
    f"http://{host}:{port}"
    for host in ("localhost", "127.0.0.1", "192.168.16.132")
    for port in (5173, 8001, 3000)
]


def _parse_cors_origins(value: Optional[str]) -> List[str]:
    if not value:
        return list(_DEFAULT_CORS_ORIGINS)
    return [origin.strip() for origin in value.split(",") if origin.strip()]


def _parse_admin_tokens(value: Optional[str]) -> dict:
    """解析 ADMIN_TOKENS（批次6 C3 RBAC）："token1=admin,token2=operator"。

    逗号分隔的 token=角色 映射；不带 = 的裸 token 视为 admin（兼容简单配置）；
    角色值非法（非 admin/operator）时忽略该项并告警（启动不阻断，防误配炸服）。
    """
    result: dict = {}
    if not value:
        return result
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            token, role = item.split("=", 1)
            token, role = token.strip(), role.strip()
            if token and role in ("admin", "operator"):
                result[token] = role
            elif token:
                import logging

                logging.getLogger(__name__).warning(
                    "ADMIN_TOKENS 忽略非法角色项: %r", item
                )
        elif item:
            # 裸 token 兼容：视为 admin
            result[item] = "admin"
    return result


class Settings(BaseModel):
    # 运行环境标识（批次8 C7 多环境配置）：dev=本机开发 / staging=VM 联调演示 /
    # prod=正式部署。供日志/健康检查透出，方便运维区分当前环境；非法值启动即
    # 报错（防误配）。三档环境的完整差异与切换指引见 doc/deployment-environments.md
    APP_ENV: str = os.environ.get("APP_ENV", "dev").strip().lower()
    APP_NAME: str = os.environ.get("APP_NAME", "景区导览AI数字人")
    DEBUG: bool = os.environ.get("DEBUG", "false").lower() == "true"

    DATABASE_URL: str = os.environ.get(
        "DATABASE_URL", "sqlite+aiosqlite:///./digital_human.db"
    )
    CORS_ORIGINS: List[str] = _parse_cors_origins(os.environ.get("CORS_ORIGINS"))
    # 是否允许携带凭证（Cookie/Authorization）的跨域请求。CORS 规范下白名单模式
    # 不能与 allow_origins=["*"] 混用（Starlette 在 credentials=True 时会把 "*"
    # 退化为回显任意 origin，等于全放开）；本服务需要 JWT Bearer 透传，故固定
    # 开启并靠 CORS_ORIGINS 白名单 + 启动校验兜底（见 _validate_cors_config）。
    CORS_ALLOW_CREDENTIALS: bool = (
        os.environ.get("CORS_ALLOW_CREDENTIALS", "true").lower() == "true"
    )

    # 管理端 RBAC 角色映射（批次6 C3 安全加固）："token1=admin,token2=operator"
    # 逗号分隔；裸 token（无 = 角色）视为 admin；ADMIN_API_TOKEN 始终视为 admin
    # （向后兼容）。角色分级：admin（全部操作）/ operator（查询/公告发布等常规
    # 操作，删除类敏感操作 403）。前端暂未改，仍传同一 token。
    ADMIN_TOKENS: dict = _parse_admin_tokens(os.environ.get("ADMIN_TOKENS"))

    # 认证端点限流（批次6 C3 安全加固，进程内实现零新依赖，见 app/core/rate_limiter.py）
    # 仅作用于 /api/v1/auth/login|register|guest，不误伤对话等正常接口。
    # 语义：滑动窗口内失败次数达到阈值后进入冷却期，冷却期内该维度直接 429。
    AUTH_RATE_LIMIT_ENABLED: bool = (
        os.environ.get("AUTH_RATE_LIMIT_ENABLED", "true").lower() == "true"
    )
    # 窗口内允许的最大失败次数（默认 5 次/分钟）
    AUTH_RATE_LIMIT_MAX_FAILURES: int = int(
        os.environ.get("AUTH_RATE_LIMIT_MAX_FAILURES", "5")
    )
    # 失败计数滑动窗口（秒），默认 60
    AUTH_RATE_LIMIT_WINDOW_SECONDS: int = int(
        os.environ.get("AUTH_RATE_LIMIT_WINDOW_SECONDS", "60")
    )
    # 触发限流后的冷却期（秒），默认 900（15 分钟）
    AUTH_RATE_LIMIT_COOLDOWN_SECONDS: int = int(
        os.environ.get("AUTH_RATE_LIMIT_COOLDOWN_SECONDS", "900")
    )
    # guest 端点纯 IP 维度固定窗口请求频率上限（次/分钟，默认 30；guest 无失败
    # 场景，用频率限制防 token 刷取滥用）
    AUTH_GUEST_MAX_PER_MINUTE: int = int(
        os.environ.get("AUTH_GUEST_MAX_PER_MINUTE", "30")
    )

    DIFY_API_URL: str = os.environ.get("DIFY_API_URL", "")
    DIFY_API_KEY: str = os.environ.get("DIFY_API_KEY", "")
    DIFY_LIGHT4AI_USER: List[str] = [
        u.strip()
        for u in os.environ.get("DIFY_LIGHT4AI_USER", "YOUR_DIFY_USER").split(",")
        if u.strip()
    ]

    OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")
    OPENAI_API_BASE: str = os.environ.get("OPENAI_API_BASE", "")
    OPENAI_MODEL: str = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    # 对话 LLM（阶段1: Dify 迁往 LangChain agent 的模型接入层，见 app/core/llm.py）
    # 与 Embedding（EMBEDDING_*）及运营分析（OPENAI_*）的配置完全独立
    # 主端点默认 opencode-go（OpenAI 兼容），备用端点为官方 DeepSeek API
    LLM_API_BASE: str = os.environ.get(
        "LLM_API_BASE", "https://opencode.ai/zen/go/v1"
    )
    LLM_API_KEY: str = os.environ.get("LLM_API_KEY", "")
    LLM_MODEL: str = os.environ.get("LLM_MODEL", "deepseek-v4-flash")
    # 备用端点（主端点不可用/未配置 key 时切换）
    LLM_FALLBACK_API_BASE: str = os.environ.get(
        "LLM_FALLBACK_API_BASE", "https://api.deepseek.com/v1"
    )
    LLM_FALLBACK_API_KEY: str = os.environ.get("LLM_FALLBACK_API_KEY", "")
    LLM_FALLBACK_MODEL: str = os.environ.get(
        "LLM_FALLBACK_MODEL", "deepseek-chat"
    )
    # 对话默认温度（工厂层默认值，可按调用覆盖）
    LLM_TEMPERATURE: float = float(os.environ.get("LLM_TEMPERATURE", "0.7"))

    # 对话引擎（A3 起主链路为 deepagents 装配层，Dify 兜底，见
    # app/services/deepagents/agent.py；chat_agent 自研链路已弃用，保留参考实现）
    # langchain: 默认，deepagents 装配层（LLM_* 配置 + 本地工具 + 记忆四工具，
    #            异常自动回落 Dify）
    # dify: 强制走 Dify Chat Agent（原链路，RAG 预检索 + Dify）
    DIALOGUE_ENGINE: str = os.environ.get("DIALOGUE_ENGINE", "langchain")

    # 阶段3 T1 双模式（方案B：简单问答走快速路径，复杂任务走规划路径，
    # 见 app/services/intent_classifier.py 与 app/services/planner_agent.py）
    # **A3 起仅对已弃用的 chat_agent 双模式链路生效**：对话主链路已切换到
    # deepagents 装配层（app/services/deepagents/），无条件执行意图分流——
    # 简单问答不注入规划指令，复杂任务注入 prompt 级 _PLAN_INSTRUCTION
    # （该开关对主链路无效，勿依赖其作为生产回退开关）
    # AGENT_DUAL_MODE=true（默认）：弃用链路按意图分类分流——简单（单工具可答）
    # 走现有 create_agent 快速路径（零延迟增加）；复杂（多步骤意图，如规划行程）
    # 走 LangGraph 规划图（规划→多步执行→总结，SSE 步骤可视化）；分类失败/
    # 规划路径异常自动降级快速路径，再由路由层兜底 Dify（双轨回落不变）
    # AGENT_DUAL_MODE=false：弃用链路全部走快速路径，行为与阶段2 完全一致
    AGENT_DUAL_MODE: bool = os.environ.get("AGENT_DUAL_MODE", "true").lower() == "true"

    # A4 成本/速度调优（deepagents 装配层组合拳，见 doc/deepagents-tuning.md）
    # 1) 工具调用次数上限：ToolCallLimitMiddleware 单轮（单次对话）全部工具
    #    调用上限；超限后继续拦截并以错误 ToolMessage 回给模型，模型基于
    #    已有信息直接作答（优雅降级不报错）。初始值 15 依据 A1 实测
    #    （复杂任务平均 9 步、峰值 13 步），A4 实测后校准（见 doc）
    AGENT_MAX_TOOL_CALLS: int = int(os.environ.get("AGENT_MAX_TOOL_CALLS", "15"))
    # 2) 工具结果归一化缓存：同查询不重查（景点/路线静态 JSON 数据可安全
    #    长缓存；知识检索内容可变——短 TTL + 索引重建即时失效）
    AGENT_TOOL_CACHE_ENABLED: bool = (
        os.environ.get("AGENT_TOOL_CACHE_ENABLED", "true").lower() == "true"
    )
    # 静态数据工具（search_spots/search_routes/get_spot_detail/get_route_detail/
    # filter_by_tags/recommend_routes）缓存 TTL（秒）：数据源为 app/data/ 静态
    # JSON（仅随版本变更），长缓存安全
    AGENT_TOOL_CACHE_TTL_STATIC: int = int(
        os.environ.get("AGENT_TOOL_CACHE_TTL_STATIC", "3600")
    )
    # 知识检索工具（search_knowledge）缓存 TTL（秒）：知识库内容可通过管理
    # 后台 CRUD 变更，短 TTL 兜底 + 索引重建时版本失效（见 tool_cache.py）
    AGENT_TOOL_CACHE_TTL_KNOWLEDGE: int = int(
        os.environ.get("AGENT_TOOL_CACHE_TTL_KNOWLEDGE", "300")
    )
    # 3) 长会话上下文压缩（回答后触发）：多轮历史消息数超过
    #    AGENT_COMPACT_TRIGGER_MESSAGES 时，压缩早期轮次为摘要、保留最近
    #    AGENT_COMPACT_KEEP_MESSAGES 条完整消息（阈值按 2 条/轮折算：
    #    24 条 ≈ 12 轮触发、10 条 ≈ 5 轮保留，A4 实测校准见 doc）
    AGENT_COMPACT_ENABLED: bool = (
        os.environ.get("AGENT_COMPACT_ENABLED", "true").lower() == "true"
    )
    AGENT_COMPACT_TRIGGER_MESSAGES: int = int(
        os.environ.get("AGENT_COMPACT_TRIGGER_MESSAGES", "24")
    )
    AGENT_COMPACT_KEEP_MESSAGES: int = int(
        os.environ.get("AGENT_COMPACT_KEEP_MESSAGES", "10")
    )

    # RAG 向量化 Embedding（与对话 LLM 的 LLM_* 及运营分析的 OPENAI_* 完全独立）
    # 不配置 EMBEDDING_API_KEY 时自动降级为本地 TF-IDF 向量化（见 app/core/embedding.py）
    # EMBEDDING_API_STYLE 控制请求体格式（2026-08-07 从 Minimax 迁移到 Qwen/DashScope 实测）：
    #   minimax（默认，兼容旧配置）: 原生格式 {"model", "texts", "type"}（type=query|db）
    #   openai: OpenAI 兼容格式 {"model", "input": [...]}（DashScope compatible-mode，
    #     实测 qwen3.7-text-embedding 1024 维，端点 https://dashscope.aliyuncs.com/compatible-mode/v1）
    EMBEDDING_API_BASE: str = os.environ.get(
        "EMBEDDING_API_BASE", "https://api.minimaxi.com/v1"
    )
    EMBEDDING_API_KEY: str = os.environ.get("EMBEDDING_API_KEY", "")
    EMBEDDING_MODEL: str = os.environ.get("EMBEDDING_MODEL", "embo-01")
    EMBEDDING_API_STYLE: str = os.environ.get("EMBEDDING_API_STYLE", "minimax")

    SSH_HOST: str = os.environ.get("SSH_HOST", "")
    SSH_PORT: int = int(os.environ.get("SSH_PORT", "22"))
    SSH_USER: str = os.environ.get("SSH_USER", "root")
    SSH_PASSWORD: str = os.environ.get("SSH_PASSWORD", "")

    # 高德 Web 服务 Key（批次8 B3：GPS 地理编码/路径规划，见
    # app/services/amap/ 与 doc/amap-gps-api.md）——演示主推「描述位置 →
    # 地理编码 → 路径规划 → 地图展示」，坐标全部来自高德 API（结构化零
    # 幻觉）；与前端地图渲染的 JS API key（VITE_AMAP_JS_KEY）是两套独立
    # 凭据。未配置时不报错：对话工具返回 amap_not_configured 错误码
    # （不阻断对话），部署 .env 已配置真实 key
    AMAP_WEB_KEY: str = os.environ.get("AMAP_WEB_KEY", "")

    ADMIN_API_TOKEN: str = os.environ.get("ADMIN_API_TOKEN", "")

    # 管理员 agent（批次6 B4）：删除类工具两阶段二次确认的待确认项 TTL（秒）
    # 首次删除调用仅登记待确认项（含 operation_id 与目标摘要），短 TTL 过期后
    # confirm_operation 失效，需重新发起（防模型误删/操作滞留，见
    # app/services/admin_agent/confirm.py）
    ADMIN_AGENT_CONFIRM_TTL_SECONDS: int = int(
        os.environ.get("ADMIN_AGENT_CONFIRM_TTL_SECONDS", "120")
    )

    # JWT 用户认证（注册/登录/游客 token，见 app/core/security.py）
    # JWT_SECRET: 生产环境必须设置为随机长字符串；留空时启动生成随机临时密钥（重启失效）
    JWT_SECRET: str = os.environ.get("JWT_SECRET", "")
    # 登录用户 token 有效期（分钟），默认 1440（24 小时）
    JWT_EXPIRES_MINUTES: int = int(os.environ.get("JWT_EXPIRES_MINUTES", "1440"))
    # 游客 token 有效期（分钟），短期即可，默认 30 分钟
    GUEST_TOKEN_EXPIRES_MINUTES: int = int(
        os.environ.get("GUEST_TOKEN_EXPIRES_MINUTES", "30")
    )

    # 短期记忆持久化（阶段2 T1：会话历史 SQLite 落盘 + 进程内 TTL 缓存，
    # 见 app/core/memory_store.py / app/tasks/memory_cleanup.py）
    # 登录用户与游客均享短期记忆（会话级，按 conversation_id）
    # 存储文件独立于主库 digital_human.db（标准库 sqlite3，无额外依赖）
    MEMORY_DB_PATH: str = os.environ.get("MEMORY_DB_PATH", "memory_store.db")
    # 会话 TTL（天）：滑动窗口，每次对话刷新过期时间；默认 7 天
    MEMORY_TTL_DAYS: int = int(os.environ.get("MEMORY_TTL_DAYS", "7"))
    # 过期会话清理周期（秒）：后台任务定期清理（启动时立即清理一次），默认 1 小时
    MEMORY_CLEANUP_INTERVAL_SECONDS: int = int(
        os.environ.get("MEMORY_CLEANUP_INTERVAL_SECONDS", "3600")
    )

    # 长期记忆（阶段2 T2：对话完成后提取用户事实/偏好，写入 user_memory 向量库，
    # 见 app/services/memory_service.py 与 doc/long-term-memory.md）
    # MEMORY_ENABLED: 记忆提取总开关（默认开启；生产可按 LLM 成本关闭）
    MEMORY_ENABLED: bool = os.environ.get("MEMORY_ENABLED", "true").lower() == "true"
    # 每用户记忆条数上限（超限淘汰 timestamp 最旧条目，防记忆无限膨胀）
    MEMORY_MAX_PER_USER: int = int(os.environ.get("MEMORY_MAX_PER_USER", "200"))

    # 长期记忆检索注入（阶段2 T3：对话首轮按 user 检索用户画像注入 agent 上下文，
    # 见 app/services/chat_agent.py 与 doc/long-term-memory.md）
    # MEMORY_INJECT_ENABLED: 注入总开关（默认开启；游客无记忆天然不注入；
    # 检索异常/无记忆静默降级不阻断对话）
    MEMORY_INJECT_ENABLED: bool = (
        os.environ.get("MEMORY_INJECT_ENABLED", "true").lower() == "true"
    )
    # 注入的记忆最大时效（天）：超过视为陈旧事实不注入（防过时信息误导）
    MEMORY_MAX_AGE_DAYS: int = int(os.environ.get("MEMORY_MAX_AGE_DAYS", "180"))

    # ── LLM token 记账（批次7 C5，见 app/services/usage_tracker.py 与 doc/usage-tracking.md） ──
    # 总开关：关闭后 llm.py 工厂不再包装埋点模型，所有调用零额外开销
    USAGE_TRACKING_ENABLED: bool = (
        os.environ.get("USAGE_TRACKING_ENABLED", "true").lower() == "true"
    )
    # 后台批量写库周期（秒）：进程内队列由 lifespan 注册的 flush 循环定时落库，
    # 避免每次调用同步写库拖慢响应；关闭应用时兜底 flush 一次
    USAGE_FLUSH_INTERVAL_SECONDS: int = int(
        os.environ.get("USAGE_FLUSH_INTERVAL_SECONDS", "5")
    )
    # 进程内队列上限：超出后立即兜底落库（不再排队，防内存膨胀）
    USAGE_QUEUE_MAX_SIZE: int = int(
        os.environ.get("USAGE_QUEUE_MAX_SIZE", "2000")
    )
    # 流式调用是否请求 usage（stream_options.include_usage）：
    # opencode-go / DeepSeek 官方均支持（实测 opencode-go 返回流式 usage）；
    # 若部署到不兼容的网关可关闭（关闭后流式调用拿不到 usage，无 token 明细且不记账）
    USAGE_STREAM_USAGE: bool = (
        os.environ.get("USAGE_STREAM_USAGE", "true").lower() == "true"
    )
    # 模型单价表（可选，订阅制可留空只记 token 不记钱）：
    # 格式 "模型:每百万输入tokens价,每百万输出tokens价"，多项分号分隔，如
    # "deepseek-v4-flash:1.0,2.0;deepseek-chat:1.0,2.0"（单位：元）
    MODEL_PRICES: str = os.environ.get("MODEL_PRICES", "")


    @model_validator(mode="after")
    def _validate_app_env(self) -> "Settings":
        """校验 APP_ENV 取值（批次8 C7）：仅允许 dev/staging/prod 三档。

        非法值（如 production/大写/笔误）启动即报错，防「以为在 prod 实际是
        dev」的误配导致行为差异不可察觉。
        """
        if self.APP_ENV not in ("dev", "staging", "prod"):
            raise ValueError(
                f"APP_ENV 非法值 {self.APP_ENV!r}（仅允许 dev/staging/prod，"
                "见 doc/deployment-environments.md）"
            )
        return self

    @model_validator(mode="after")
    def _validate_cors_config(self) -> "Settings":
        """CORS 白名单校验（批次6 C3）：credentials 模式禁止与通配符 * 混用。

        Starlette 在 allow_credentials=True 且 allow_origins=["*"] 时会退化为
        「回显任意请求 origin」——等于全放开，白名单形同虚设。此处启动即报错，
        要求显式收紧为具体 origin 列表（或关闭 credentials）。
        """
        if self.CORS_ALLOW_CREDENTIALS and "*" in self.CORS_ORIGINS:
            raise ValueError(
                "CORS_ORIGINS 不能包含通配符 *（CORS_ALLOW_CREDENTIALS=true 时"
                " Starlette 会将 * 退化为回显任意 origin = 全放开）；请配置具体"
                " origin 白名单（逗号分隔），或显式设置 CORS_ALLOW_CREDENTIALS=false"
            )
        return self

    @model_validator(mode="after")
    def _validate_dify_config(self) -> "Settings":
        """Validate that Dify API config values are non-empty at startup."""
        # 阶段4（2026-08-06 船长决策方案A）：Dify Workflow 量化评估已本地化
        # （app/services/evaluation_service.py），DIFY_WORKFLOW_API_KEY 已移除；
        # 剩余 DIFY_API_URL/DIFY_API_KEY 服务于对话双轨回落 + 历史数据接口
        if not self.DIFY_API_URL:
            raise ValueError("DIFY_API_URL must not be empty")
        if not self.DIFY_API_KEY:
            raise ValueError("DIFY_API_KEY must not be empty")
        return self


settings = Settings()
