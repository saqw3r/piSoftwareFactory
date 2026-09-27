"""Review (ADR-0007): fresh-context, read-only, verdict `clean|nits|blockers`.

The reviewer sees the lane diff against main and a strict rubric; it never
shares conversation context with the worker (cross-model-style isolation is
achieved by a separate pi process with restricted tools).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ..config import FactoryConfig
from ..integrations.pi_agent import run_reviewer
from .graph import Node

VERDICTS = ("clean", "nits", "blockers")
_VERDICT_RE = re.compile(r"verdict\W*[:\-]?\W*(clean|nits|blockers)", re.IGNORECASE)
# Fallback for models that answer "✅ CLEAN" instead of the exact format:
# word-bounded keyword, last occurrence wins, searched only in the tail
# where the verdict line lives (prose earlier may mention 'blockers').
_KEYWORD_RE = re.compile(r"\b(clean|nits|blockers)\b", re.IGNORECASE)


def parse_verdict(text: str) -> str:
    if not text:
        return ""
    strict = _VERDICT_RE.findall(text)
    if strict:
        return strict[-1].lower()
    tail = "\n".join(text.splitlines()[-15:])
    loose = _KEYWORD_RE.findall(tail)
    return loose[-1].lower() if loose else ""

# Small local models (a 9B Qwen at 65k ctx is the owner's reality) respond
# conversationally to role-based prompts ("what would you like me to do?").
# This rubric is deliberately imperative, output-format-first, and forbids
# questions; the diff is inlined (never @-includes: POSIX paths don't
# resolve on Windows inside pi).
REVIEW_PROMPT = """\
TASK: Review the diff between the DIFF-START and DIFF-END markers at the end of this message. Do not ask questions. Do not describe what you are about to do. Review it now and output your result immediately.

Check for: (1) correctness bugs, (2) scope creep beyond the stated element, (3) weakened tests or gates (a test changed to make it pass is a blocker), (4) missing docs/commit hygiene.

Output format — use exactly this structure:
Findings:
- <finding or "none">
Verdict: clean

(Use `Verdict: nits` for minor non-blocking issues, `Verdict: blockers` when the change must not merge.)

Element: {title} ({kind}, risk {risk}).

DIFF-START
```diff
{diff}
```
DIFF-END
"""


def lane_diff(root: Path, node: Node, main_branch: str) -> str:
    proc = subprocess.run(
        ("git", "-C", str(root), "diff", f"{main_branch}...HEAD"),
        capture_output=True,
        text=True,
        check=False,
    )
    diff = proc.stdout or ""
    if len(diff.splitlines()) > 400:  # context discipline (ADR-0010)
        lines = diff.splitlines()
        diff = "\n".join([*lines[:250], f"... [{len(lines) - 300} diff lines elided] ...", *lines[-50:]])
    return diff


def review_node(root: Path, node: Node, config: FactoryConfig, cwd: Path | None = None) -> Node:
    diff = lane_diff(cwd or root, node, config.release.main_branch)
    prompt = REVIEW_PROMPT.format(
        title=node.title,
        kind=node.kind,
        risk=node.risk,
        main_branch=config.release.main_branch,
        diff=diff or "(no diff yet)",
    )
    # Small local models sample inconsistently; a no-verdict response is
    # retried once with a fresh context before being reported as such.
    for attempt in range(2):
        run = run_reviewer(prompt, config, cwd=cwd or root)
        node.verdict = parse_verdict(run.final_text or "")
        node.notes = (run.final_text or "")[-2000:]
        if node.verdict:
            break
        if attempt == 0:
            prompt = REVIEW_PROMPT.format(
                title=node.title,
                kind=node.kind,
                risk=node.risk,
                main_branch=config.release.main_branch,
                diff=diff or "(no diff yet)",
            ) + "\nIMPORTANT: The diff IS included above between DIFF-START and DIFF-END. Review it now. Do not ask for anything. End with `Verdict: clean`, `Verdict: nits` or `Verdict: blockers`."
    else:
        node.verdict = ""
        node.notes = (run.final_text or "")[-2000:]
    node.touch()
    return node
