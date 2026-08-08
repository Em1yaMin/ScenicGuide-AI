"""LLM token 记账核心（批次7 C5）。

设计（船长决策：token 记账放后台、分模型/链路、可查询详细）：

- **统一埋点**：`UsageTrackingChatOpenAI`（ChatOpenAI 子类）在模型调用层
  拦截——所有经 `llm.py` 工厂（get_chat_llm/create_chat_llm）构造的模型
  调用自动记账，调用方零改动、不改变 LLM 调用行为（仅流式时附带
  `stream_options.include_usage` 请求 usage，opencode-go/DeepSeek 实测支持）
- **链路/用户上下文**：`usage_context(chain, user)` contextmanager 由各
  调用点包住（contextvars 传播，asyncio.to_thread / langgraph 图内任务均
  继承），模型调用结束后据此标记记录归属
- **批量 flush**：进程内线程安全队列（queue.Queue）+ lifespan 注册的后台
  flush 循环（`flush_loop`）定时批量写库——对话响应不因写库拖慢；队列满
  时立即兜底落库（run_coroutine_threadsafe 调度 flush）并丢弃最旧条目防
  内存膨胀；应用关闭时 `shutdown()` 兜底 flush 一次
- **费用估算（可选）**：`MODEL_PRICES` 单价表（元/百万 tokens）换算 cost；
  订阅制（opencode-go）可留空只记 token 不记钱（cost 列留 NULL）

记账字段：model / input_tokens / output_tokens / total_tokens / duration_ms
/ chain / user / cost / created_at（llm_usage 表，见 app/models/usage.py）。
"""
import asyncio
import logging
import queue
import time
from contextvars import ContextVar
from contextlib import contextmanager
from functools import lru_cache
from typing import Any, Dict, Iterator, List, Optional

from langchain_openai import ChatOpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── 链路常量（报表聚合维度，契约见 doc/usage-tracking.md） ────────────────

CHAIN_DIALOGUE = "对话"          # deepagents 对话主链路（含弃用 chat_agent）
CHAIN_RECOMMEND = "推荐"         # 推荐问句生成（阶段3 T3 本地 LLM）
CHAIN_EVALUATION = "评估"        # 量化评估（阶段4 本地 LLM，analysis 链路）
CHAIN_COMPACTION = "压缩"        # 长会话上下文压缩摘要（A4）
CHAIN_ADMIN_AGENT = "管理员agent"  # 管理员 agent（批次6 B4）
CHAIN_MEMORY = "记忆提取"        # 长期记忆事实提取（阶段2 T2）
CHAIN_PLANNER = "规划"           # 弃用链路 planner_agent（规划图）
CHAIN_OTHER = "其他"             # 未标注链路的兜底

# 允许的链路值（报表按此聚合；非法值归一为 CHAIN_OTHER）
_CHAIN_ALIASES = {
    CHAIN_DIALOGUE, CHAIN_RECOMMEND, CHAIN_EVALUATION, CHAIN_COMPACTION,
    CHAIN_ADMIN_AGENT, CHAIN_MEMORY, CHAIN_PLANNER,
}

# ── 链路/用户上下文（contextvars：图内任务/to_thread 自动继承） ───────────

_chain_var: ContextVar[str] = ContextVar("usage_chain", default=CHAIN_OTHER)
_user_var: ContextVar[str] = ContextVar("usage_user", default="")


@contextmanager
def usage_context(
    chain: str, user: Optional[str] = None
) -> Iterator[None]:
    """标注当前 LLM 调用的链路来源与用户（contextmanager）。

    调用点把模型调用（invoke/ainvoke/astream/图执行）包在 `with
    usage_context(CHAIN_*, user):` 内；contextvars 语义保证：异步任务、
    asyncio.to_thread 线程、langgraph 图内子任务均继承上下文，模型调用
    结束后据此记账。

    Args:
        chain: 链路常量（CHAIN_*）；非法值记账时归一为 CHAIN_OTHER
        user: 用户标识（登录用户 id / 游客 id / visitor）；后台任务传 None
    """
    t_chain = _chain_var.set(chain)
    t_user = _user_var.set(user or "")
    try:
        yield
    finally:
        _chain_var.reset(t_chain)
        _user_var.reset(t_user)


def _current_chain() -> str:
    chain = _chain_var.get()
    return chain if chain in _CHAIN_ALIASES else CHAIN_OTHER


def _current_user() -> Optional[str]:
    return _user_var.get() or None


# ── 单价表解析（可选；订阅制可留空） ──────────────────────────────────────

@lru_cache(maxsize=8)
def _parse_model_prices(raw: str) -> Dict[str, tuple[float, float]]:
    """解析 MODEL_PRICES："模型:输入价,输出价"（元/百万 tokens），模型项用分号分隔。

    例如 "deepseek-v4-flash:1.0,2.0;deepseek-chat:1.0,2.0"；非法项忽略告警。
    """
    result: Dict[str, tuple[float, float]] = {}
    if not raw:
        return result
    for item in raw.split(";"):
        item = item.strip()
        if not item:
            continue
        if ":" not in item:
            logger.warning("MODEL_PRICES 忽略非法项（缺冒号分隔）: %r", item)
            continue
        model, prices = item.split(":", 1)
        parts = [p.strip() for p in prices.split(",")]
        if len(parts) != 2:
            logger.warning("MODEL_PRICES 忽略非法项（需 输入价,输出价 两项）: %r", item)
            continue
        try:
            result[model.strip()] = (float(parts[0]), float(parts[1]))
        except ValueError:
            logger.warning("MODEL_PRICES 忽略非法价格: %r", item)
    return result


def _compute_cost(
    model: str, input_tokens: int, output_tokens: int
) -> Optional[float]:
    """按单价表估算费用（元）；模型未配置单价/未配置单价表返回 None。"""
    prices = _parse_model_prices(settings.MODEL_PRICES)
    entry = prices.get(model)
    if entry is None:
        return None
    in_price, out_price = entry
    return round(input_tokens / 1e6 * in_price + output_tokens / 1e6 * out_price, 6)


# ── 记账队列 + 批量 flush ─────────────────────────────────────────────────


class UsageTracker:
    """进程内记账队列（线程安全）+ 后台批量落库。

    线程安全模型：模型调用发生在任意线程（事件循环线程 / asyncio.to_thread
    工作线程 / langgraph 工具线程池），`record_usage` 经 queue.Queue 入队
    零阻塞；flush 循环（事件循环线程）定时 drain 批量写库。
    """

    def __init__(self) -> None:
        self._queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(
            maxsize=max(settings.USAGE_QUEUE_MAX_SIZE, 1)
        )
        # 已绑定的事件循环（record 队列满时兜底调度立即 flush）
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # 关闭标记：shutdown 后不再写库（丢弃新记录）
        self._closed = False
        # 统计（观测/测试用）
        self.recorded = 0        # 入队总数
        self.flushed = 0         # 成功落库总数
        self.failed = 0          # 写库失败丢弃总数
        self.dropped = 0         # 队列满丢弃最旧总数

    # ── 对外入口 ──

    def record_usage(
        self,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        duration_ms: int,
        chain: Optional[str] = None,
        user: Optional[str] = None,
    ) -> None:
        """记录一次 LLM 调用（非阻塞入队；队列满时兜底落库/丢最旧）。

        chain/user 未显式传入时取当前 usage_context 上下文（默认其他/空）。
        """
        if not settings.USAGE_TRACKING_ENABLED:
            return
        if self._closed:
            logger.debug("usage 记账已关闭，丢弃记录: model=%s", model)
            return
        if chain is None or chain not in _CHAIN_ALIASES:
            # 未显式指定或非法值：取调用点 usage_context 上下文（已归一）
            chain = _current_chain()
        record: Dict[str, Any] = {
            "model": model,
            "input_tokens": max(int(input_tokens or 0), 0),
            "output_tokens": max(int(output_tokens or 0), 0),
            "total_tokens": max(int(input_tokens or 0), 0) + max(int(output_tokens or 0), 0),
            "duration_ms": max(int(duration_ms or 0), 0),
            "chain": chain,
            "user": (user or _current_user()),
            "cost": _compute_cost(model, int(input_tokens or 0), int(output_tokens or 0)),
            "created_at": None,  # ORM default 填 UTC now
        }
        try:
            self._queue.put_nowait(record)
            self.recorded += 1
        except queue.Full:
            # 兜底：调度一次立即 flush 腾空队列（不阻塞当前调用线程）
            self._try_immediate_flush()
            try:
                self._queue.put_nowait(record)
                self.recorded += 1
            except queue.Full:
                # 仍满（flush 尚未执行）：丢弃最旧条目腾位，新记录不丢
                try:
                    self._queue.get_nowait()
                    self.dropped += 1
                except queue.Empty:
                    pass
                self._queue.put_nowait(record)
                self.recorded += 1

    def _try_immediate_flush(self) -> bool:
        """队列满兜底：向绑定的事件循环调度一次立即 flush（尽力而为）。"""
        loop = self._loop
        if loop is None or loop.is_closed():
            return False
        try:
            asyncio.run_coroutine_threadsafe(self.flush_now(), loop)
            return True
        except RuntimeError:
            return False

    # ── 落库 ──

    async def flush_now(self, session_factory: Optional[Any] = None) -> int:
        """drain 队列并批量写库（单事务），返回落库条数。

        写库失败（锁/磁盘等）仅告警并丢弃本批（防死循环重试拖垮启动/
        关闭路径）；由下一轮 flush 继续处理新记录。
        """
        rows: List[Dict[str, Any]] = []
        while True:
            try:
                rows.append(self._queue.get_nowait())
            except queue.Empty:
                break
        if not rows:
            return 0
        factory = session_factory or _session_factory
        try:
            from app.models.usage import LLMUsage

            async with factory() as session:
                session.add_all([LLMUsage(**r) for r in rows])
                await session.commit()
            self.flushed += len(rows)
            return len(rows)
        except Exception as e:
            logger.warning("usage 批量写库失败（丢弃 %d 条）: %s", len(rows), e)
            self.failed += len(rows)
            return 0

    async def flush_loop(self) -> None:
        """后台 flush 循环（main.py lifespan 注册）：绑定事件循环 + 定时落库。"""
        self._loop = asyncio.get_running_loop()
        try:
            while True:
                await asyncio.sleep(settings.USAGE_FLUSH_INTERVAL_SECONDS)
                await self.flush_now()
        finally:
            self._loop = None

    async def shutdown(self) -> None:
        """应用关闭兜底：最后 flush 一次并关闭记账（之后新记录丢弃）。"""
        await self.flush_now()
        self._closed = True

    # ── 测试/管理辅助 ──

    def reset(self, maxsize: Optional[int] = None) -> None:
        """清空队列与统计（测试用；同时恢复记账开启状态）。"""
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._loop = None
        self._closed = False
        self.recorded = self.flushed = self.failed = self.dropped = 0
        if maxsize is not None:
            self._queue = queue.Queue(maxsize=maxsize)

    def pending_count(self) -> int:
        """当前队列待落库条数（测试/观测用）。"""
        return self._queue.qsize()


# 模块级单例（llm.py 工厂 / lifespan / 调用点共用）
usage_tracker = UsageTracker()

# 落库会话工厂（可注入；默认走主库 async_session_factory）
from app.core.database import async_session_factory as _session_factory  # noqa: E402


# ── 模型调用层统一埋点（ChatOpenAI 子类，经 llm.py 工厂构造） ────────────


def _usage_from_llm_output(llm_output: Optional[Dict[str, Any]]) -> Optional[Dict]:
    """从非流式 ChatResult.llm_output 提取 token 用量（OpenAI 兼容格式）。"""
    if not llm_output:
        return None
    usage = llm_output.get("token_usage") or {}
    if not isinstance(usage, dict):
        return None
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    if prompt is None and completion is None:
        return None
    return {"input": prompt or 0, "output": completion or 0}


def _usage_from_metadata(usage_metadata: Optional[Dict[str, Any]]) -> Optional[Dict]:
    """从流式 chunk 的 usage_metadata 提取 token 用量（langchain 归一格式）。"""
    if not usage_metadata:
        return None
    input_tokens = usage_metadata.get("input_tokens")
    output_tokens = usage_metadata.get("output_tokens")
    if input_tokens is None and output_tokens is None:
        return None
    return {"input": input_tokens or 0, "output": output_tokens or 0}


class UsageTrackingChatOpenAI(ChatOpenAI):
    """ChatOpenAI 子类：模型调用层统一记账（token/耗时/链路/用户）。

    覆盖四个模型调用入口（非流式 _generate/_agenerate、流式 _stream/
    _astream），正常完成后向 usage_tracker 入队一条记录；调用行为不变：

    - 非流式：响应 llm_output["token_usage"]（OpenAI 兼容 usage 字段）
    - 流式：请求附带 `stream_options.include_usage`（USAGE_STREAM_USAGE
      开关，默认开；opencode-go/DeepSeek 实测支持），从最后一个带
      usage_metadata 的 chunk 取用量
    - 调用异常/流被中断（未收到 usage）：不记账（无用量数据，宁缺毋滥）

    chain/user 取自调用点 usage_context 上下文（见 usage_context）。
    """

    def _generate(
        self, messages, stop=None, run_manager=None, **kwargs
    ) -> Any:
        start = time.perf_counter()
        result = super()._generate(
            messages, stop=stop, run_manager=run_manager, **kwargs
        )
        usage = _usage_from_llm_output(getattr(result, "llm_output", None))
        if usage is not None:
            _record_usage(self, usage, start)
        return result

    async def _agenerate(
        self, messages, stop=None, run_manager=None, **kwargs
    ) -> Any:
        start = time.perf_counter()
        result = await super()._agenerate(
            messages, stop=stop, run_manager=run_manager, **kwargs
        )
        usage = _usage_from_llm_output(getattr(result, "llm_output", None))
        if usage is not None:
            _record_usage(self, usage, start)
        return result

    def _stream(self, messages, stop=None, run_manager=None, **kwargs) -> Iterator:
        # 流式 usage：调用方未显式指定时按配置请求（include_usage）
        if kwargs.get("stream_usage") is None:
            kwargs["stream_usage"] = settings.USAGE_STREAM_USAGE
        start = time.perf_counter()
        last_usage: Optional[Dict] = None
        for chunk in super()._stream(
            messages, stop=stop, run_manager=run_manager, **kwargs
        ):
            usage = _usage_from_metadata(
                getattr(getattr(chunk, "message", None), "usage_metadata", None)
            )
            if usage is not None:
                last_usage = usage
            yield chunk
        if last_usage is not None:
            _record_usage(self, last_usage, start)

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs) -> Any:
        if kwargs.get("stream_usage") is None:
            kwargs["stream_usage"] = settings.USAGE_STREAM_USAGE
        start = time.perf_counter()
        last_usage: Optional[Dict] = None
        async for chunk in super()._astream(
            messages, stop=stop, run_manager=run_manager, **kwargs
        ):
            usage = _usage_from_metadata(
                getattr(getattr(chunk, "message", None), "usage_metadata", None)
            )
            if usage is not None:
                last_usage = usage
            yield chunk
        if last_usage is not None:
            _record_usage(self, last_usage, start)


def _record_usage(llm: ChatOpenAI, usage: Dict[str, int], start: float) -> None:
    """包装模型调用结果入队记账（model/耗时来自实例与计时）。"""
    duration_ms = int((time.perf_counter() - start) * 1000)
    usage_tracker.record_usage(
        model=getattr(llm, "model_name", "") or "",
        input_tokens=usage.get("input", 0),
        output_tokens=usage.get("output", 0),
        duration_ms=duration_ms,
    )
