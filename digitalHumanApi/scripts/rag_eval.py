#!/usr/bin/env python
"""RAG 景区知识问答评测 CLI（离线可复跑）。

对评测集（doc/rag-eval/eval_set.json）逐题检索并判定，输出 Markdown 报告。
默认本地 TF-IDF（完全离线、可复现）；部署环境可 --backend api 使用配置的
远端 Embedding（注意结果分数分布不同，拒答阈值需用 --threshold 校准）。

用法:
    python -m scripts.rag_eval                                     # 默认跑全量并打印报告
    python -m scripts.rag_eval --out doc/rag-eval/report_round1.md # 报告写入文件
    python -m scripts.rag_eval --top-k 5 --backend tfidf           # 调整检索参数
    python -m scripts.rag_eval --threshold 0.25                    # 覆盖拒答阈值

退出码 0 表示运行成功（无论分数）；--assert-accuracy 0.9 时低于目标退出码 2。
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.rag_eval import (  # noqa: E402
    DEFAULT_EVAL_SET,
    load_eval_set,
    render_markdown_report,
    run_eval,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--eval-set", type=Path, default=DEFAULT_EVAL_SET, help="评测集 JSON 路径"
    )
    parser.add_argument(
        "--out", type=Path, default=None, help="Markdown 报告输出路径（默认 stdout）"
    )
    parser.add_argument("--top-k", type=int, default=3, help="检索候选数（默认 3）")
    parser.add_argument(
        "--backend",
        choices=["tfidf", "api"],
        default="tfidf",
        help="embedding 后端（默认 tfidf=完全离线；api=settings 配置的远端 Embedding）",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="拒答判定 top1 分数阈值（默认按后端自适应）",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="结构化结果 JSON 输出路径（默认不输出）",
    )
    parser.add_argument(
        "--assert-accuracy",
        type=float,
        default=None,
        help="低于该准确率时退出码 2（用于验收门禁）",
    )
    args = parser.parse_args()

    eval_set = load_eval_set(args.eval_set)
    report = run_eval(
        cases=eval_set["cases"],
        top_k=args.top_k,
        threshold=args.threshold,
        backend=args.backend,
    )
    report["eval_set"] = str(args.eval_set)

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"结构化结果已写入: {args.json_out}")

    markdown = render_markdown_report(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(markdown, encoding="utf-8")
        print(f"报告已写入: {args.out}")
    else:
        print(markdown)

    accuracy = report["totals"]["accuracy"]
    print(f"\n准确率: {accuracy * 100:.1f}%")
    if args.assert_accuracy is not None and accuracy < args.assert_accuracy:
        print(
            f"[FAIL] 准确率 {accuracy * 100:.1f}% 低于目标 "
            f"{args.assert_accuracy * 100:.1f}%"
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
