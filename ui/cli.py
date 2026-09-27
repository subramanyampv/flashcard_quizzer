"""UI Agent — terminal interface (design doc §3).

All quiz rules & parsing stay in their owning agents; this file only drives
the orchestrator and paints the terminal.

    python -m ui.cli --file data/sample_decks/python_basics.json --mode adaptive

Commands at the prompt:  <answer> | hint | skip | next-mode? no — keep simple:
  hint, skip, results, help, quit   (also: just type an answer)
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from agent.core_agent import MultiAgentOrchestrator
from config import PROJECT_ROOT, Config, ConfigError

RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
RED, GREEN, YELLOW, CYAN, MAGENTA = "\033[31m", "\033[32m", "\033[33m", "\033[36m", "\033[35m"

HELP = f"""{BOLD}Commands{RESET}
  <text>     grade as your answer
  hint       ask for a nudge (never the answer)
  skip       give up on the current card
  results    current score
  quit       end the session"""


def _run_gate() -> Config:
    """Tier-3 startup gate with a clean pass table (blueprint contract)."""
    try:
        cfg = Config.load()
        report = cfg.run_startup_gate()
    except ConfigError as exc:
        print(f"{RED}{BOLD}Startup gate FAILED:{RESET}\n{exc}")
        sys.exit(1)
    print(report.render())
    return cfg


async def _interact(orch: MultiAgentOrchestrator, cfg: Config) -> None:
    def say(color: str, text: str) -> None:
        print(f"{color}{text}{RESET}")

    say(DIM, "Upload/load a deck first. Use --file, or type: load <path>")
    while True:
        try:
            raw = input(f"{BOLD}you ›{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            say(DIM, "\nbye.")
            break
        if not raw:
            continue
        low = raw.lower()
        if low in ("quit", "exit", "q"):
            break
        if low == "help":
            print(HELP)
            continue
        if low == "results":
            if orch.engine is not None:
                s = orch.engine.score()
                say(CYAN, f"answered {s.answered} · correct {s.correct} · "
                          f"accuracy {round(s.accuracy * 100)}% · streak {s.streak}")
            else:
                say(DIM, "No quiz in progress.")
            continue

        if low.startswith("load "):
            r = await orch.process("load_deck", {"file_source": low[5:].strip()})
        elif orch.engine is None and orch.deck and low in ("sequential", "random", "adaptive"):
            r = await orch.process("start_quiz", {"mode": low})
        elif low == "hint":
            r = await orch.process("hint", {})
        elif orch.engine is None and orch.deck:
            say(YELLOW, "Deck loaded. Pick a mode: sequential | random | adaptive")
            continue
        elif orch.engine is None:
            say(YELLOW, "No deck yet — type: load <path/to/deck.json>")
            continue
        elif low == "skip":
            r = await orch.process("answer", {"answer": "skip"})
        else:
            r = await orch.process("answer", {"answer": raw})

        if r["next_step"] == "start_quiz":
            say(GREEN, r["response"])
            continue
        if r["data"].get("finished"):
            say(BOLD + MAGENTA, r["response"])
            break
        verdict = r["data"].get("verdict")
        if verdict:
            color = GREEN if verdict["correct"] else (YELLOW if verdict["partial"] else RED)
            tag = "✓" if verdict["correct"] else ("◐" if verdict["partial"] else "✗")
            note = f" {DIM}[{verdict['graded_by']}]{RESET}" if verdict["graded_by"] else ""
            say(color, f"{tag} {r['response'].splitlines()[0]}{note}")
        else:
            say(CYAN if r["confidence"] else RED, r["response"].splitlines()[0])
        score = r["data"].get("score")
        if score:
            say(DIM, f"  score {score['correct']}/{score['answered']} · "
                     f"acc {round(score['accuracy'] * 100)}% · streak {score['streak']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Flashcard Quizzer — terminal UI")
    parser.add_argument("--file", help="deck file to load (json/csv/txt/md)")
    parser.add_argument("--format", default="", help="force format: json|csv|txt|md")
    parser.add_argument("--mode", default="", choices=["", "sequential", "random", "adaptive"])
    parser.add_argument("--seed", type=int, default=None, help="shuffle seed (testing)")
    args = parser.parse_args(argv)

    cfg = _run_gate()
    print(HELP)

    async def go() -> None:
        orch = MultiAgentOrchestrator(config=cfg)
        if args.file:
            r = await orch.process("load_deck", {"file_source": args.file, "file_format": args.format})
            print((GREEN if r["confidence"] else RED) + r["response"] + RESET)
            if r["confidence"] and args.mode:
                r = await orch.process("start_quiz", {"mode": args.mode, "seed": args.seed})
                print(CYAN + r["response"] + RESET)
        await _interact(orch, cfg)

    try:
        asyncio.run(go())
        return 0
    except ConfigError as exc:
        print(f"{RED}{exc}{RESET}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
