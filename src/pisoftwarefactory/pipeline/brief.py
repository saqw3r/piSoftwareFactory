"""Brief rendering (ADR-0007/ADR-0010): a worker brief is ≤ ~2k tokens —
node description, explicit files, gate expectations, recalled memories,
and the worker contract. Hindsight recall is best-effort: the brief is
complete without it.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Template

from ..pipeline.graph import Node

BRIEF_TEMPLATE = """\
# Element {{ node.id }}: {{ node.title }}

- kind: {{ node.kind }} · risk: {{ node.risk }} · size: {{ node.size }} · language: {{ node.language or "n/a" }}
{% if node.files %}
- files you may touch (plus what they genuinely require):
{% for f in node.files %}  - @{{ f }}
{% endfor %}
{% endif %}
## Task

{{ node.task }}

{% if memories %}
## Relevant memory (Hindsight recall)

{% for m in memories %}- {{ m }}
{% endfor %}
{% endif %}
{% if gate_feedback %}
## Previous gate failure (fix this first)

```
{{ gate_feedback }}
```
{% endif %}
## Contract (AGENTS.md applies in full)

1. 1 element = 1 commit. Commit message: what changed and why.
2. Run `GIT_EDITOR=true` on git commands that could open an editor;
   `git show --stat HEAD` after committing.
3. The gates decide success. When they fail, read the output above, fix, commit.
4. Do not touch `{{ main_branch }}`; you are on lane `{{ lane }}`.
"""


def render_brief(
    node: Node,
    lane: str,
    main_branch: str,
    memories: list[str] | None = None,
    gate_feedback: str = "",
) -> str:
    return Template(BRIEF_TEMPLATE).render(
        node=node,
        lane=lane,
        main_branch=main_branch,
        memories=memories or [],
        gate_feedback=gate_feedback.strip(),
    )


def write_brief(briefs_dir: Path, node: Node, content: str) -> Path:
    briefs_dir.mkdir(parents=True, exist_ok=True)
    path = briefs_dir / f"{node.id}.md"
    path.write_text(content, encoding="utf-8", newline="\n")
    return path
