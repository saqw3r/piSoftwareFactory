"""Review (ADR-0007): fresh-context, read-only, verdict `clean|nits|blockers`.

The reviewer sees the lane diff against main and a strict rubric; it never
shares conversation context with the worker (cross-model-style isolation is
achieved by a separate pi process with restricted tools).
"""

from __future__ import annotations

import re
from pathlib import Path

from .. import gitcmd
from ..config import FactoryConfig
from ..integrations.pi_agent import brain_label, run_reviewer
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


def _new_files(wt: Path, max_files: int = 5, max_lines: int = 120) -> str:
    """Untracked files shown by content, because a worker may write and never commit.

    For "produce a .md" the deliverable is exactly the file `git diff base...HEAD`
    does not contain until someone commits it — and the reviewer was being handed
    ``(no diff yet)`` over a finished document.
    """
    proc = gitcmd.run(wt, "status", "--porcelain", "--untracked-files=all", check=False)
    if proc.returncode != 0:
        return ""
    blocks: list[str] = []
    for line in proc.stdout.splitlines():
        path = line[2:].strip().strip('"')
        if not line.startswith("??") or path.startswith(".factory/"):
            continue
        try:
            text = (wt / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines()
        body = "\n".join(lines[:max_lines]) + ("\n… truncated …" if len(lines) > max_lines else "")
        blocks.append(f"--- new file: {path}\n{body}")
        if len(blocks) >= max_files:
            break
    return "\n".join(blocks)


def lane_diff(root: Path, node: Node, main_branch: str) -> str:
    """The lane's change — committed diff plus whatever the worker left uncommitted.

    An empty diff and a refused ``git diff`` used to be the same string here, and
    the reviewer was asked to review it — which is how a lane with no work got a
    verdict.
    """
    proc = gitcmd.run(root, "diff", f"{main_branch}...HEAD")
    diff = (proc.stdout or "").strip()
    uncommitted = _new_files(root)
    if uncommitted:
        diff = f"{diff}\n\n{uncommitted}".strip()
    if len(diff.splitlines()) > 400:  # context discipline (ADR-0010)
        lines = diff.splitlines()
        diff = "\n".join([*lines[:250], f"... [{len(lines) - 300} diff lines elided] ...", *lines[-50:]])
    return diff


def review_node(root: Path, node: Node, config: FactoryConfig, cwd: Path | None = None,
                log=None) -> Node:
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
        if log:
            log(f"  reviewer turn {attempt + 1}: {config.harness.name} reading "
                f"{len(diff or '')} chars of diff ({brain_label(config)})")
        run = run_reviewer(prompt, config, cwd=cwd or root, log=log)
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
