import asyncio
import logging
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.models.dialogue import Dialogue
from app.models.evaluation import Evaluation
from app.services import evaluation_service
from app.services.evaluation_service import normalize_string_list

logger = logging.getLogger(__name__)

MAX_BATCH_SIZE = 50  # Limit evaluations per cycle
# 本地 LLM 评估并发上限（opencode-go 延迟高：并发压满反而互相拖慢/超时，
# 适度并发 + 单条超时预算即可；单条超时见 evaluation_service._EVAL_TIMEOUT）
EVAL_CONCURRENCY = 4


# 复用 evaluation_service 导出的归一化助手（strip/去重/空返 None），
# 消除重复实现；保留本名符号兼容（tests/test_analysis.py 直接引用）
_normalize_string_list = normalize_string_list


async def evaluate_dialogue(
    db: AsyncSession, dialogue: Dialogue
) -> Optional[Evaluation]:
    """Evaluate a single dialogue via local LLM (阶段4：Dify Workflow 评估已本地化).

    复用 `app/services/evaluation_service.py`（LLM_* 配置 opencode-go，prompt 语义
    对齐原 Dify 量化工作流）。评估失败（LLM 未配置/调用异常/解析失败）静默返回
    None——analysis_loop 本轮跳过该对话，下轮自动重试，不阻断任务。

    Args:
        db: 会话（保留签名兼容，评估本身不访问 DB）
        dialogue: 待评估对话（读取 content）
    """
    parsed = await evaluation_service.evaluate_content(dialogue.content)
    if parsed is None:
        return None

    # Map LLM output fields to Evaluation columns（与旧 Dify 版字段映射保持一致）
    evaluation = Evaluation(
        dialogue_id=dialogue.id,
        sentiment_label=parsed.get("sentiment_label"),
        satisfaction_score=parsed.get("satisfaction_score"),
        concern_tags=parsed.get("concern_tags"),
        mentioned_spots=_normalize_string_list(parsed.get("mentioned_spots")),
        raw_result=parsed,
    )
    return evaluation


async def batch_evaluate() -> int:
    """Run one batch evaluation cycle. Returns number evaluated.

    分三阶段：
    1. 查未评估对话（LEFT JOIN evaluation IS NULL）
    2. **并发**调本地 LLM 评估内容（asyncio.gather + 信号量限流；evaluate_dialogue
       只读 dialogue.content 并构造脱库 ORM 对象，无并发 DB 访问，共享会话安全；
       单条评估经 evaluation_service 内 asyncio.to_thread 派发，事件循环不阻塞）
    3. 串行去重校验 + 写 Evaluation 并提交

    任何单条评估失败静默降级（返回 None/异常均跳过），不阻断整体批次。
    """
    async with async_session_factory() as db:
        # Find unevaluated dialogues (LEFT JOIN where evaluation IS NULL)
        result = await db.execute(
            select(Dialogue)
            .outerjoin(
                Evaluation, Evaluation.dialogue_id == Dialogue.id
            )
            .where(
                Evaluation.id.is_(None),
                Dialogue.content != "",
            )
            .limit(MAX_BATCH_SIZE)
        )
        dialogues: List[Dialogue] = list(result.scalars().all())

        if not dialogues:
            logger.debug("No dialogues pending evaluation")
            return 0

        # Phase 2: 并发本地 LLM 评估（限流，防端点过载/超时连锁）
        semaphore = asyncio.Semaphore(EVAL_CONCURRENCY)

        async def _evaluate_one(d: Dialogue) -> Optional[Evaluation]:
            async with semaphore:
                return await evaluate_dialogue(db, d)

        results = await asyncio.gather(
            *(_evaluate_one(d) for d in dialogues),
            return_exceptions=True,
        )

        # Phase 3: 写库（串行；单条失败静默跳过，下轮重试）
        evaluated = 0
        for dialogue, result in zip(dialogues, results):
            if isinstance(result, Exception):
                logger.warning(
                    "评估异常（跳过，下轮重试）dialogue=%d: %s",
                    dialogue.id,
                    result,
                )
                continue
            if result is None:
                continue
            # Double-check to avoid IntegrityError on unique constraint
            existing = await db.execute(
                select(Evaluation).where(
                    Evaluation.dialogue_id == dialogue.id
                )
            )
            if existing.scalar_one_or_none() is not None:
                continue

            db.add(result)
            evaluated += 1

        if evaluated > 0:
            try:
                await db.commit()
                logger.info(
                    "Evaluated %d dialogues in batch", evaluated
                )
            except Exception:
                await db.rollback()
                logger.warning(
                    "Evaluation commit rolled back, will retry next cycle"
                )
                return 0

        return evaluated


async def analysis_loop() -> None:
    """Run the analysis loop every 5 minutes until cancelled."""
    logger.info(
        "Analysis task started (interval: 300s, max batch: %d)",
        MAX_BATCH_SIZE,
    )
    try:
        while True:
            try:
                await batch_evaluate()
            except Exception:
                logger.exception(
                    "Analysis cycle failed, will retry in 300s"
                )
            await asyncio.sleep(300)
    except asyncio.CancelledError:
        logger.info("Analysis task cancelled, shutting down")
