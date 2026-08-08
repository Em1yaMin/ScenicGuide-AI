"""deepagents 记忆工具化四工具（A2 试点 → A3 主链路，批次3，船长决策：user_id 隔离）。

与阶段2 的「对话后后台提取 + 对话前检索注入」（memory_service / chat_agent
记忆钩子）不同，本模块把长期记忆暴露为 agent 可直接调用的四个工具，模型
按需主动读写（记忆工具化，A3 主链路切换的决策前置验证）：

    recall_user_memory(关键词)  — 语义检索当前用户记忆（top_k=3）
    save_user_memory(事实)      — 写入/覆盖当前用户记忆
    list_user_memories()        — 列出当前用户全部记忆（含 id）
    delete_user_memory(id)      — 按 id 删除当前用户记忆

**user_id 隔离机制**：全部工具经 `InjectedState("user")` 注解注入当前对话
用户——langgraph 从图状态（graph state 的 `user` 字段，由装配层入口从对话
上下文解析后传入）注入，模型看不到注入参数（工具 schema 对模型隐藏），
用户也无法经文本指定他人 user（工具函数只接受注入值）；游客
（visitor / guest-*）一律跳过读写（memory_service 契约）。管理员版全量
记忆管理为后续 B5 任务，本任务只做用户侧。

轻量用户摘要（昵称/记忆概览）仍由装配层注入 system prompt（防"忘记调用"
记忆工具），见 `agent.py::_build_light_summary`。
"""
import json
import logging
from typing import Annotated, Any, Dict, List

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from app.services import memory_service

logger = logging.getLogger(__name__)

# 记忆工具步骤展示文案（SSE step 事件透出，装配层合并进 _STEP_LABELS）
MEMORY_STEP_LABELS: Dict[str, str] = {
    "recall_user_memory": "正在回忆您的偏好…",
    "save_user_memory": "正在记住您的偏好…",
    "list_user_memories": "正在整理您的记忆…",
    "delete_user_memory": "正在删除记忆…",
}

# 单次检索返回条数上限（与对话前注入 top_k 一致）
_RECALL_TOP_K = 3
# 单次写入事实条数上限（防模型一次性灌入超长列表）
_SAVE_MAX_FACTS = 10
# 单条记忆内容长度上限：与 memory_service._MEMORY_MAX_CONTENT_LEN 契约一致
# （记忆 schema content ≤200 字符，见 doc/long-term-memory.md；模型经工具
# 写入同样受限，与对话后提取路径 _parse_facts 截断行为对齐）
_CONTENT_MAX_LEN = 200
# 兼容旧条目（无 memory_id）的派生 id 前缀，见 list_user_memories
_LEGACY_ID_PREFIX = "legacy:"


def _entry_to_json(entry: memory_service.MemoryEntry) -> Dict[str, Any]:
    """MemoryEntry → 工具返回 JSON 字段（含 id，供 delete 使用）。

    旧条目（升级前写入、无 memory_id）派生稳定 id `legacy:{归一化 content}`
    （同 user 同内容去重覆盖，派生 id 唯一且稳定，可正常按 id 删除）。
    """
    memory_id = entry.memory_id
    if not memory_id:
        memory_id = (
            f"{_LEGACY_ID_PREFIX}{memory_service._normalize_content(entry.content)}"
        )
    return {
        "id": memory_id,
        "fact_type": entry.fact_type,
        "content": entry.content,
        "timestamp": entry.timestamp,
    }


def _empty_result(message: str) -> str:
    return json.dumps({"message": message}, ensure_ascii=False)


@tool
async def recall_user_memory(
    query: str, user: Annotated[str, InjectedState("user")]
) -> str:
    """检索当前用户在历史对话中表达过的长期记忆（偏好/事实/习惯）。

    当用户提及自己的偏好、出行人群、既往经历等需要个性化回答的内容时调用；
    返回与 query 最相关的记忆条目 JSON 列表（id/fact_type/content/timestamp）。
    记忆条目由系统在对话后自动提取保存，也可能由 save_user_memory 写入。"""
    if not query or not query.strip():
        return _empty_result("请提供检索关键词")
    try:
        entries = await memory_service.search_user_memory(
            user, query, top_k=_RECALL_TOP_K
        )
    except Exception as e:
        logger.warning("recall_user_memory 工具失败（静默降级）: %s", e)
        return _empty_result("记忆检索暂时不可用")
    if not entries:
        return _empty_result("暂无相关记忆")
    return json.dumps([_entry_to_json(e) for e in entries], ensure_ascii=False)


@tool
async def save_user_memory(
    facts: str, user: Annotated[str, InjectedState("user")]
) -> str:
    """把用户明确表达的新事实/偏好写入其长期记忆。

    facts 为 JSON 数组（每项 {"fact_type": "preference|fact|habit",
    "content": "记忆句"}）或单个自然语言记忆句（按 fact 类型保存）。
    同内容重复保存会覆盖更新（不膨胀）；游客用户自动跳过。"""
    if not facts or not facts.strip():
        return _empty_result("没有可保存的内容")
    entries = _parse_facts_input(facts)
    if not entries:
        return _empty_result("无法解析要保存的事实（需为 JSON 数组或自然语言句）")
    result = await memory_service.save_user_memory(user, entries)
    if result.get("skipped"):
        return _empty_result("游客用户不保存长期记忆")
    return json.dumps(result, ensure_ascii=False)


def _parse_facts_input(facts: str) -> List[memory_service.MemoryEntry]:
    """解析 save_user_memory 的输入：JSON 数组优先，自然语言句兜底。

    启发式：含 `[`/`{` 的文本（疑似 JSON 残片）解析失败视为非法输入返回空；
    其余按自然语言单句保存（fact 类型）。
    """
    text = facts.strip()
    # 1. JSON 数组：[{"fact_type": "...", "content": "..."}, ...]
    try:
        data = json.loads(text)
        if isinstance(data, list):
            entries: List[memory_service.MemoryEntry] = []
            for item in data[:_SAVE_MAX_FACTS]:
                if not isinstance(item, dict):
                    continue
                content = str(item.get("content", "") or "").strip()
                if not content:
                    continue
                # 内容长度截断：与 memory_service._parse_facts 提取路径一致
                # （记忆 schema 契约 content ≤200 字符，防模型经工具写入超长句）
                if len(content) > _CONTENT_MAX_LEN:
                    content = content[:_CONTENT_MAX_LEN]
                entries.append(
                    memory_service.MemoryEntry(
                        fact_type=str(item.get("fact_type", "fact") or "fact"),
                        content=content,
                    )
                )
            if entries:
                return entries
            # 空数组/全非法项：视为无可保存内容
            return []
        # JSON 非数组（对象/数字等）：不按自然语言句保存（模型传错格式）
        return []
    except (json.JSONDecodeError, TypeError):
        pass
    # 2. 疑似 JSON 残片（含括号但解析失败）：非法输入，不保存
    if "[" in text or "{" in text:
        return []
    # 3. 自然语言句：单个事实（类型归一 fact；同样截断到内容长度上限）
    if len(text) > _CONTENT_MAX_LEN:
        text = text[:_CONTENT_MAX_LEN]
    return [memory_service.MemoryEntry(fact_type="fact", content=text)]


@tool
async def list_user_memories(
    user: Annotated[str, InjectedState("user")]
) -> str:
    """列出当前用户的全部长期记忆条目（含 id，供 delete_user_memory 使用）。

    当需要了解用户全部已知信息（如回答前先看画像）时调用；
    返回 JSON 列表（id/fact_type/content/timestamp）。"""
    try:
        entries = await memory_service.list_user_memories(user)
    except Exception as e:
        logger.warning("list_user_memories 工具失败（静默降级）: %s", e)
        return _empty_result("记忆读取暂时不可用")
    if not entries:
        return _empty_result("暂无记忆")
    return json.dumps([_entry_to_json(e) for e in entries], ensure_ascii=False)


@tool
async def delete_user_memory(
    memory_id: str, user: Annotated[str, InjectedState("user")]
) -> str:
    """按 id 删除当前用户的一条长期记忆（仅能删除自己的记忆，user 隔离）。

    memory_id 来自 list_user_memories 返回的 id 字段；删除后返回
    {"deleted": 1}；id 不存在或属于其他用户时返回 {"deleted": 0}。"""
    if not memory_id or not memory_id.strip():
        return _empty_result("请提供要删除的记忆 id")
    try:
        result = await memory_service.delete_user_memory(user, memory_id.strip())
    except Exception as e:
        logger.warning("delete_user_memory 工具失败（静默降级）: %s", e)
        return _empty_result("记忆删除暂时不可用")
    return json.dumps(result, ensure_ascii=False)
