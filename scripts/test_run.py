"""End-to-end demonstration + gate: Manager → Data → Quiz → QA (design doc §4).

    python scripts/test_run.py [--quick]

Walks the full pipeline: startup gate, a clean deck quiz (adaptive), the
design-doc §5 corrupted-file scenario, then hands everything to the QA agent.
Exits non-zero unless every stage passes — the "manager closes on QA PASS"
rule made executable.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent.agents.qa_agent import QAAgent  # noqa: E402
from agent.core_agent import MultiAgentOrchestrator  # noqa: E402
from config import Config, ConfigError  # noqa: E402

FIXTURES = PROJECT_ROOT / "fixtures"


def banner(text: str) -> None:
    print(f"\n{'=' * 62}\n{text.center(62)}\n{'=' * 62}")


async def stage_config_gate() -> Config:
    banner("STAGE 1 — Config & LLM access gate")
    cfg = Config.load()
    report = cfg.run_startup_gate()
    print(report.render())
    return cfg


async def stage_quiz(orch: MultiAgentOrchestrator) -> None:
    banner("STAGE 2 — Manager → Data → Quiz (adaptive session)")
    r = await orch.process("load_deck", {"file_source": str(FIXTURES / "valid_deck.json")})
    print("load_deck  →", r["response"].splitlines()[0])
    assert r["confidence"] == 1.0 and orch.engine is None  # quiz not auto-started
    r = await orch.process("start_quiz", {"mode": "adaptive", "seed": 2})
    print("start_quiz →", r["response"])
    seen, guard = {}, 0
    while not orch.engine.finished and guard < 30:
        guard += 1
        card = orch.engine.next_card
        seen[card.id] = seen.get(card.id, 0) + 1
        answer = card.answer if seen[card.id] >= 2 else "not sure at all"
        r = await orch.process("answer", {"answer": answer})
        v = r["data"].get("verdict") or {}
        tag = "✓" if v.get("correct") else ("◐" if v.get("partial") else "✗")
        print(f"{tag} [{card.id}] {card.question[:40]:<42} → {r['response'].splitlines()[0][:60]}")
    print("summary    →", r["response"])
    assert r["data"].get("finished"), "quiz never finished"


async def stage_bad_file(orch: MultiAgentOrchestrator) -> None:
    banner("STAGE 3 — design doc §5: corrupted upload never reaches quiz")
    orch.__init__(orch.config)  # fresh session
    engine_before = orch.engine
    r = await orch.process("load_deck", {"file_source": str(FIXTURES / "corrupted_syntax.json")})
    print("response   →", r["response"].replace("\n", " | ")[:200])
    assert orch.engine is engine_before, "QUIZ WAS INVOKED ON BAD FILE"
    assert r["confidence"] == 0.0 and "JSON" in r["response"]
    print("boundary   → OK: quiz agent never called")


async def stage_qa(quick: bool) -> None:
    banner("STAGE 4 — QA agent gate")
    qa = QAAgent()
    report = await qa.run("eval" if quick else "all")
    print(qa.render(report))
    assert report["status"] == "PASS", f"QA FAILED: {report}"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true", help="skip pytest subprocess (eval only)")
    args = parser.parse_args()

    cfg = await stage_config_gate()
    orch = MultiAgentOrchestrator(cfg)
    await stage_quiz(orch)
    await stage_bad_file(orch)
    await stage_qa(args.quick)
    banner("RESULT — all stages green")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except ConfigError as exc:
        print(f"\nCONFIG HALT: {exc}")
        raise SystemExit(2)
    except AssertionError as exc:
        print(f"\nGATE VIOLATION: {exc}")
        raise SystemExit(3)
