"""Typed intake decisions: hosted Jev vs local model vs heuristic (research task).

Runs every task in tasks.jsonl through the available legs and prints a
comparison table against the expected labels. Legs:

- heuristic  — pisoftwarefactory.pipeline.intake.heuristic_intake (always)
- local      — the OpenAI-compatible endpoint on :8080 asked for strict JSON
               with confidence + abstain (the reference design for a future
               provider="local"; works with any model the router serves)
- jev        — hosted typesafe.ai; needs `pip install jev` + Python >= 3.14 +
               TYPESAFE_API_KEY. Verified 2026-11-05: jev 0.3.0 discards
               probabilities ("no confidence or abstention reach the caller"),
               so the ADR-0004 sketch (confidence + abstain) does not match
               the real package — this leg is design-only until that changes.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from pisoftwarefactory.config import FactoryConfig
from pisoftwarefactory.pipeline.intake import heuristic_intake

HERE = pathlib.Path(__file__).parent
ENDPOINT = os.environ.get("INTAKE_ENDPOINT", "http://127.0.0.1:8080/v1/chat/completions")
MODEL = os.environ.get("INTAKE_MODEL", "bonsai-27b")
ABSTAIN_THRESHOLD = 0.6

PROMPT = """You are the intake classifier of an AI software factory.
Classify the task. Answer with ONLY a JSON object, no other text:
{{"kind": "feature|bug|chore|docs", "risk": "low|standard|high", "size": "small|medium|large", "language": "csharp|python|js|rust|cpp|go|", "confidence": 0.0, "reason": "one short sentence"}}
Rules: language is "" when the task does not indicate a language. risk=high for
security/auth/payments/migrations/deletes. If less than {t:.0%} sure, set
"kind" to "abstain" and say what is missing in "reason".
Task: {task}"""


def load_tasks() -> list[dict]:
    return [json.loads(line) for line in (HERE / "tasks.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


def run_heuristic(tasks: list[dict]) -> dict[str, dict]:
    config = FactoryConfig(languages=["csharp"])
    return {t["task"]: heuristic_intake(t["task"], config).model_dump() for t in tasks}


def run_local(tasks: list[dict]) -> dict[str, dict]:
    out = {}
    for t in tasks:
        body = json.dumps({
            "model": MODEL,
            "messages": [{"role": "user", "content": PROMPT.format(t=ABSTAIN_THRESHOLD, task=t["task"])}],
            "temperature": 0,
            "max_tokens": 2000,
        }).encode()
        req = urllib.request.Request(ENDPOINT, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                content = json.load(resp)["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = content.split("```")[1].removeprefix("json").strip()
            d = json.loads(content)
            try:
                d["confidence"] = float(d.get("confidence", 1))
            except (TypeError, ValueError):
                d["confidence"] = 1.0
            if d["confidence"] < ABSTAIN_THRESHOLD and d.get("kind") != "abstain":
                d["kind"] = "abstain"
            out[t["task"]] = {**d, "provider": "local"}
        except Exception as exc:  # noqa: BLE001 — research harness: report, don't crash
            out[t["task"]] = {"kind": "error", "reason": str(exc)[:120], "provider": "local"}
    return out


def run_jev(tasks: list[dict]) -> dict[str, dict]:
    import importlib.util
    reasons = []
    if importlib.util.find_spec("jev") is None:
        reasons.append("jev package not installed")
    if not os.environ.get("TYPESAFE_API_KEY"):
        reasons.append("TYPESAFE_API_KEY not set")
    if sys.version_info < (3, 14):
        reasons.append(f"python {sys.version_info[:3]} < 3.14 (jev requirement)")
    note = "; ".join(reasons) or "available"
    return {t["task"]: {"kind": "skipped", "reason": note, "provider": "jev"} for t in tasks}


def agreement(decision: dict, task: dict) -> str:
    checks = []
    for field in ("kind", "risk", "size"):
        want = task.get(f"expected_{field}")
        if want:
            checks.append(decision.get(field) == want)
    return "pass" if all(checks) else "MISS" if checks else "n/a"


def main() -> None:
    tasks = load_tasks()
    legs = {"heuristic": run_heuristic(tasks), "local": run_local(tasks), "jev": run_jev(tasks)}
    print(f"{'task':<44} {'expected':<8} | {'heuristic':<9} | {'local':<26} | jev")
    print("-" * 110)
    for t in tasks:
        short = t["task"][:41] + "…" if len(t["task"]) > 41 else t["task"]
        want = t.get("expected_kind", "—")
        h, l, j = legs["heuristic"][t["task"]], legs["local"][t["task"]], legs["jev"][t["task"]]
        mark = lambda d, task: f"{d.get('kind','?')}{'' if agreement(d, task) != 'MISS' else ' ✗'}"  # noqa: E731
        conf = f" ({l.get('confidence')})" if isinstance(l.get("confidence"), (int, float)) else (f" ERR: {l.get('reason', '')[:60]}" if l.get("kind") == "error" else "")
        print(f"{short:<44} {want:<8} | {mark(h, t):<9} | {mark(l, t):<20}{conf:<6} | {j.get('kind')} {j.get('reason', '')[:40]}")
    for name in ("heuristic", "local"):
        scored = [agreement(legs[name][t["task"]], t) for t in tasks if t.get("expected_kind")]
        hits = scored.count("pass")
        print(f"{name}: {hits}/{len(scored)} expected-label hits")
    abstains = sum(1 for d in legs["local"].values() if d.get("kind") == "abstain")
    print(f"local abstained on {abstains} task(s) — abstention → heuristic per run_intake")


if __name__ == "__main__":
    main()
