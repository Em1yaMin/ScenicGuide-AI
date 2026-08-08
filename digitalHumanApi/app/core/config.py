import os
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, model_validator


_env_path = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_env_path, override=True)


def _parse_cors_origins(value: Optional[str]) -> List[str]:
    if not value:
        return ["http://localhost:5173"]
    return [origin.strip() for origin in value.split(",") if origin.strip()]


class Settings(BaseModel):
    APP_NAME: str = os.environ.get("APP_NAME", "景区导览AI数字人")
    DEBUG: bool = os.environ.get("DEBUG", "false").lower() == "true"

    DATABASE_URL: str = os.environ.get(
        "DATABASE_URL", "sqlite+aiosqlite:///./digital_human.db"
    )
    CORS_ORIGINS: List[str] = _parse_cors_origins(os.environ.get("CORS_ORIGINS"))

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

    # 对话引擎（阶段1 T2：LangChain agent 主链路，Dify 兜底，见 app/services/chat_agent.py）
    # langchain: 默认，LangChain agent（LLM_* 配置 + 本地工具，异常自动回落 Dify）
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

    ADMIN_API_TOKEN: str = os.environ.get("ADMIN_API_TOKEN", "")

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
