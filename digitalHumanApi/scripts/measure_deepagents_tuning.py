"""deepagents 装配层 A4 调优实测脚本（轻量真实 LLM 调用，验证组合拳效果）。

与 A1 基线对比（A1 数据见 doc/deepagents-tuning.md 与 data/sg-deepagents-probe/）：
- simple：首字延迟 / 总耗时 / input+output token / 工具调用次数
- complex：同上（注入规划指令，走真实工具链）

用法（需 digitalHumanApi/.env 已配置真实 LLM_API_KEY）：
    cd digitalHumanApi && python -m scripts.measure_deepagents_tuning

每轮度量（agent 级，直连 astream 采集）：
- first_token_ms：首个非空文本 token 到达时间（用户感知首字延迟）
- total_ms：整轮完成耗时
- input/output tokens：各模型调用 usage 去重累计（含工具链中间调用）
- tool_calls：工具调用次数（按调用 id 去重累计）
- cache_hits/cache_misses：A4 工具缓存命中统计（本轮新增量）

可选 --long-compact：14 轮简单问答，演示上下文压缩（第 12 轮起触发，
历史条数与输入 token 有界，见 doc/deepagents-tuning.md）。

结果打印 Markdown 表格，直接粘贴进 doc/deepagents-tuning.md 实测节。
"""
import argparse
import asyncio
import json
import sys
import time
from typing import Any, Dict, List

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage

from app.core.llm import get_chat_llm
from app.services import chat_agent as main_chain
from app.services.deepagents import agent as dg_agent
from app.services.deepagents import tool_cache
from app.services.deepagents.harness import _AGENT_MAX_TOKENS, _AGENT_TIMEOUT, _MAX_AGENT_STEPS

# 实测查询（与 A1 同类型：简单问答 / 复杂规划各 3 轮）
SIMPLE_QUERIES = [
    "你好，能介绍一下自己吗？",
    "景区几点开放？门票多少钱？",
    "灵山寺在哪里？怎么去？",
]
COMPLEX_QUERIES = [
    "帮我规划灵山一日游，带老人小孩，喜欢拍照",
    "规划两日游行程，喜欢自然风光和历史人文，预算有限",
    "帮我安排周末亲子游路线，想轻松一点，最好能看日出",
]


def _fmt(ms: float) -> str:
    return f"{ms / 1000:.2f}s" if ms >= 1000 else f"{ms:.0f}ms"


class _Metrics:
    def __init__(self) -> None:
        self.first_token_ms: float = 0.0
        self.total_ms: float = 0.0
        self.input_tokens = 0
        self.output_tokens = 0
        self.tool_calls = 0
        self.answer_len = 0
        self.cache_hits = 0
        self.cache_misses = 0


async def _run_turn(
    agent: Any,
    query: str,
    history: List[BaseMessage],
    complex_intent: bool,
) -> _Metrics:
    """单轮度量：组装消息（与主链路 _build_messages 同逻辑）→ astream 采集。"""
    messages = dg_agent._build_messages(
        query, history=history, complex_intent=complex_intent, light_summary=""
    )
    m = _Metrics()
    cache_before = tool_cache.get_cache().stats()
    parts: List[str] = []
    seen_usage: set = set()
    t0 = time.monotonic()
    async for chunk, _meta in agent.astream(
        {"messages": messages, "user": "visitor"},
        stream_mode="messages",
        config={"recursion_limit": _MAX_AGENT_STEPS},
    ):
        if isinstance(chunk, AIMessageChunk):
            text = chunk.content
            if isinstance(text, str) and text and not m.first_token_ms:
                m.first_token_ms = (time.monotonic() - t0) * 1000
            if isinstance(text, str):
                parts.append(text)
            for call in chunk.tool_call_chunks or []:
                if call.get("id") and call.get("name"):
                    m.tool_calls += 1
            um = getattr(chunk, "usage_metadata", None)
            if um:
                key = (um.get("input_tokens"), um.get("output_tokens"))
                if key not in seen_usage:
                    seen_usage.add(key)
                    m.input_tokens += um.get("input_tokens", 0) or 0
                    m.output_tokens += um.get("output_tokens", 0) or 0
    m.total_ms = (time.monotonic() - t0) * 1000
    m.answer_len = len("".join(parts))
    cache_after = tool_cache.get_cache().stats()
    m.cache_hits = cache_after["hits"] - cache_before["hits"]
    m.cache_misses = cache_after["misses"] - cache_before["misses"]
    # 多轮上下文：本轮问答入历史（与 _append_history 相同形态）
    history.append(HumanMessage(content=query))
    history.append(AIMessage(content="".join(parts)))
    return m


def _print_table(rows: List[Dict[str, Any]], title: str) -> None:
    print(f"\n## {title}\n")
    print("| 轮次 | 查询 | 首字延迟 | 总耗时 | input tok | output tok | 工具调用 | 缓存命中/未中 |")
    print("|------|------|---------|--------|-----------|------------|---------|---------------|")
    for r in rows:
        print(
            f"| {r['idx']} | {r['query'][:18]}… | {_fmt(r['first'])} | {_fmt(r['total'])} "
            f"| {r['input']} | {r['output']} | {r['tools']} | {r['hits']}/{r['misses']} |"
        )
    n = len(rows)
    avg = lambda k: sum(r[k] for r in rows) / n  # noqa: E731
    print(
        f"| 平均 | - | {_fmt(avg('first'))} | {_fmt(avg('total'))} "
        f"| {avg('input'):.0f} | {avg('output'):.0f} | {avg('tools'):.1f} | - |"
    )
    print()


async def _measure(agent: Any, queries: List[str], complex_intent: bool, tag: str) -> List[Dict[str, Any]]:
    history: List[BaseMessage] = []
    rows: List[Dict[str, Any]] = []
    for i, query in enumerate(queries, start=1):
        m = await _run_turn(agent, query, history, complex_intent)
        rows.append(
            {
                "idx": i,
                "query": query,
                "first": m.first_token_ms,
                "total": m.total_ms,
                "input": m.input_tokens,
                "output": m.output_tokens,
                "tools": m.tool_calls,
                "hits": m.cache_hits,
                "misses": m.cache_misses,
                "answer_len": m.answer_len,
            }
        )
        print(
            f"[{tag}] 轮{i} 完成: first={_fmt(m.first_token_ms)} total={_fmt(m.total_ms)} "
            f"tok={m.input_tokens}+{m.output_tokens} tools={m.tool_calls} "
            f"cache={m.cache_hits}h/{m.cache_misses}m",
            flush=True,
        )
    return rows


async def _long_compact_demo(agent: Any) -> None:
    """长会话压缩演示：14 轮简单问答，观测历史条数与输入 token 有界。

    与主链路一致：每轮回答后对本地 history 执行压缩（compaction.maybe_compact，
    第 12 轮 24 条达阈值触发），记录压缩前后历史条数与输入 token。
    """
    from app.services.deepagents import compaction

    print("\n## 长会话压缩演示（14 轮简单问答，第 12 轮起触发压缩）\n")
    history: List[BaseMessage] = []
    rows: List[Dict[str, int]] = []
    llm = get_chat_llm(max_tokens=1024, timeout=60)
    for i in range(14):
        query = f"介绍一下景区第{i + 1}个景点有什么特色" if i % 2 == 0 else "好的，请继续"
        m = await _run_turn(agent, query, history, complex_intent=False)
        # 回答后触发压缩（与 agent._maybe_compact_history 同逻辑）
        compacted = await compaction.maybe_compact(history, llm)
        if compacted is not None:
            history = compacted
        rows.append({"round": i + 1, "hist": len(history), "input": m.input_tokens})
        print(
            f"轮{i + 1}: 历史条数={len(history)} 本轮 input={m.input_tokens} "
            f"输出={m.output_tokens} 耗时={_fmt(m.total_ms)}",
            flush=True,
        )
    print(
        "\n压缩效果：第11轮历史 22 条/input ~{a} tok → 第12轮压缩后 11 条，"
        "第14轮历史 {b} 条/input {c} tok（未压缩为 28 条且线性增长）".format(
            a=rows[10]["input"], b=rows[13]["hist"], c=rows[13]["input"]
        )
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--long-compact", action="store_true", help="追加长会话压缩演示")
    parser.add_argument("--demo-only", action="store_true", help="只跑长会话压缩演示")
    parser.add_argument("--queries-json", default="", help="自定义查询 JSON 文件（可选）")
    args = parser.parse_args()

    print(f"装配参数: AGENT_MAX_TOOL_CALLS={main_chain.settings.AGENT_MAX_TOOL_CALLS} "
          f"tool_cache={'on' if main_chain.settings.AGENT_TOOL_CACHE_ENABLED else 'off'} "
          f"compact_trigger={main_chain.settings.AGENT_COMPACT_TRIGGER_MESSAGES} "
          f"compact_keep={main_chain.settings.AGENT_COMPACT_KEEP_MESSAGES}")
    print("构建 agent（真实 LLM，约 20-30s）…", flush=True)
    t0 = time.monotonic()
    agent = dg_agent.get_agent(use_rag=True)
    print(f"agent 构建完成: {(time.monotonic() - t0):.1f}s", flush=True)

    simple_q = SIMPLE_QUERIES
    complex_q = COMPLEX_QUERIES
    if args.queries_json:
        with open(args.queries_json, encoding="utf-8") as f:
            data = json.load(f)
        simple_q = data.get("simple", simple_q)
        complex_q = data.get("complex", complex_q)

    if args.demo_only:
        await _long_compact_demo(agent)
        print("\n演示完成。")
        return

    simple_rows = await _measure(agent, simple_q, complex_intent=False, tag="simple")
    _print_table(simple_rows, "简单问答路径（3 轮）")
    complex_rows = await _measure(agent, complex_q, complex_intent=True, tag="complex")
    _print_table(complex_rows, "复杂规划路径（3 轮）")

    print("## 与 A1 基线对比（平均）\n")
    print("| 路径 | 首字延迟 | 总耗时 | input tok | output tok | 工具调用 |")
    print("|------|---------|--------|-----------|------------|---------|")
    print("| A1 simple | - | 4.87s | 1372 | 287 | 0-1 |")
    print("| A4 simple | - | - | - | - | - |")
    print("| A1 complex (plan=on) | - | 33.47s | 20982 | 2922 | 9 |")
    print("| A4 complex | - | - | - | - | - |")

    if args.long_compact:
        await _long_compact_demo(agent)
    print("\n实测完成。请把表格数据填入 doc/deepagents-tuning.md 实测节。")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)
