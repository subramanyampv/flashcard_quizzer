"""DeepEval harness (blueprint §6): grade every rubric case through the real
grading pipeline and score against expectations.

Runs as a gate with a threshold: python -m evaluation.deepeval_harness
Exit 0 = grading agreement >= threshold (default 0.9). Writes logs/eval.log.
Uses the live LLM when configured, otherwise the deterministic fallback — the
rubric is designed so both paths must agree on verdicts.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict

from agent.schemas import Verdict
from config import PROJECT_ROOT, get_config
from evaluation import deepeval_metrics as metrics
from tools.implementations.grade_answer import grade_answer

RUBRIC = PROJECT_ROOT / "data" / "evaluation" / "eval_rubric.json"
EVAL_LOG = PROJECT_ROOT / "logs" / "eval.log"


async def run(threshold: float = 0.9) -> Dict[str, Any]:
    cfg = get_config()
    cases = json.loads(RUBRIC.read_text(encoding="utf-8"))
    results, passed = [], 0
    t0 = time.perf_counter()

    for case in cases:
        q, exp, given = case["input"]["question"], case["input"]["expected"], case["input"]["given"]
        result = await grade_answer(question=q, expected=exp, given=given)
        verdict = Verdict(**result["data"])
        agree = metrics.correctness_match(verdict.model_dump(), case["expected"])
        passed += int(agree)
        results.append({"id": case["id"], "agree": agree, "graded_by": verdict.graded_by,
                        "verdict": {k: getattr(verdict, k) for k in
                                    ("correct", "partial", "confidence", "needs_review")},
                        "expected": case["expected"]})

    accuracy = passed / len(cases) if cases else 0.0
    report = {
        "n_cases": len(cases), "agreements": passed, "accuracy": round(accuracy, 4),
        "threshold": threshold, "grading_mode": "fallback-keyword" if cfg.offline_mode else "openai",
        "metrics_lib": "deepeval+custom" if metrics.deepeval_available() else "custom",
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        "status": "PASS" if accuracy >= threshold else "FAIL", "cases": results,
    }
    try:
        EVAL_LOG.parent.mkdir(parents=True, exist_ok=True)
        with EVAL_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **report}) + "\n")
    except OSError:
        pass
    return report


def main() -> int:
    report = asyncio.run(run())
    width = 64
    print("=" * width)
    print("GRADE-QUALITY EVALUATION".center(width))
    print("=" * width)
    for case in report["cases"]:
        mark = "✓" if case["agree"] else "✗"
        print(f"[{mark}] {case['id']:<6} expected correct={str(case['expected']['correct']):<5} "
              f"partial={str(case['expected'].get('partial', False)):<5} → "
              f"got correct={str(case['verdict']['correct']):<5} "
              f"partial={str(case['verdict']['partial']):<5} ({case['graded_by']})")
    print("-" * width)
    print(f"Agreement: {report['agreements']}/{report['n_cases']} = "
          f"{report['accuracy'] * 100:.1f}% (threshold {report['threshold'] * 100:.0f}%)"
          f" · mode: {report['grading_mode']} · {report['elapsed_ms']} ms")
    print(f"Status: {report['status']}")
    print("=" * width)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
