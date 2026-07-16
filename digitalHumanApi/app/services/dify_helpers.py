"""Shared Dify output parsing utilities.

Used by both analysis (tasks/analysis.py) and recommend (services/recommend_service.py)
to strip <think> tags, markdown code fences, and JSON-decode Dify workflow outputs.
"""
import json
import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_THINK_PATTERN = re.compile(r"<think>.*?</think>", re.DOTALL)
_MARKDOWN_FENCE_PATTERN = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)


def parse_workflow_output(raw: Any) -> Optional[Dict[str, Any]]:
    """Parse Dify workflow text output.

    Three-layer defense:
    1. Strip <think>...</think> tags (deepseek-r1 reasoning wrapper)
    2. Extract content from ```json ... ``` markdown fences
    3. json.loads() decode

    Returns parsed dict on success, None on any failure.
    """
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        raw = str(raw)

    text = raw.strip()
    if not text:
        return None

    # 1. Strip <think>...</think> tags
    text = _THINK_PATTERN.sub("", text).strip()

    # 2. Try markdown code fence extraction
    fence_match = _MARKDOWN_FENCE_PATTERN.search(text)
    if fence_match:
        text = fence_match.group(1).strip()

    # 3. JSON decode
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.debug("Failed to parse workflow output as JSON: %s", text[:200])
        return None
