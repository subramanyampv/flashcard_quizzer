"""QA Agent — build-time + gate-time quality owner (design doc §3).

Runs the full pytest suite and the grading-quality eval harness, then reports
PASS/FAIL with a machine-readable report. The Manager treats PASS as the
condition to consider the project release-ready (design doc §4 step 9).
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from typing import Any, Dict, Optional

from config import PROJECT_ROOT


class QAAgent:
    name = "qa_agent"

    def __init__(self, eval_threshold: float = 0.9):
        self.eval_threshold = eval_threshold

    async def run(self, suite: str = "all") -> Dict[str, Any]:
        """suite: unit | eval | all → combined PASS/FAIL report."""
        report: Dict[str, Any] = {"target_suite": suite, "status": "PASS", "checks": {}}
        if suite in ("all", "unit"):
            report["checks"]["unit"] = self._run_unit_tests()
        if suite in ("all", "eval"):
            from evaluation.deepeval_harness import run as run_eval
            report["checks"]["eval"] = await run_eval(self.eval_threshold)
        for check in report["checks"].values():
            if check.get("status") != "PASS":
                report["status"] = "FAIL"
        return report

    def _run_unit_tests(self) -> Dict[str, Any]:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "tests"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=600,
        )
        tail = (proc.stdout or "").strip().splitlines()[-3:]
        passed = proc.returncode == 0
        return {"status": "PASS" if passed else "FAIL", "returncode": proc.returncode,
                "summary": " | ".join(tail)}

    def render(self, report: Optional[Dict[str, Any]] = None) -> str:
        report = report or asyncio.run(self.run())
        lines = ["QA REPORT", f"unit : {report['checks'].get('unit', {}).get('summary', 'skipped')}",
                 f"eval : {report['checks'].get('eval', {}).get('accuracy', 'skipped')} agreement",
                 f"status: {report['status']}"]
        return "\n".join(lines)


if __name__ == "__main__":
    agent = QAAgent()
    rep = asyncio.run(agent.run())
    print(agent.render(rep))
    print(json.dumps(rep, indent=2)[:1500])
    sys.exit(0 if rep["status"] == "PASS" else 1)
