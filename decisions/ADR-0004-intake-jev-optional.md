# ADR-0004: Intake = Jev (optional, advisory) with deterministic local fallback

- **Status:** Accepted
- **Date:** 2026-09-27
- **Deciders:** Serhii Surnin (owner) — explicit choice between "optional + fallback" and "skip Jev"; ZCode (facilitator)
- **Origin:** user answer to Jev-scope question; ESF precedent ("Optional DSPy/Jev intake (advisory)")

## Context

The Intake stage must turn a raw task string into structured routing data:
type (feature/bug/chore/docs), risk tier, language/lane, element sizing, and
 blocker edges for the `launch.graph.json` DAG.

**Jev** (PyPI `jev` 0.3.0; TypeSafe's "System One" decision model at
typesafe.ai) provides exactly this shape:

```python
import jev

@jev.fn
def classify_task(task: str) -> TaskClass:
    """Classify a factory task: kind, risk tier, lane, size estimate."""
```

The decorator compiles the typed function into a query; the call returns a
validated instance of the return annotation with **confidence scores and
abstention** (an uncertain decision returns `predicted: null` /
`abstained: true` instead of a fabricated answer). Abstention maps naturally
to "escalate to human at Intake".

Caveat discovered during research: Jev is a **hosted API** (needs
`TYPESAFE_API_KEY`), which conflicts with the factory's local-first principle
(ADR-0001).

## Decision

Jev is an **optional, advisory** intake enhancer:

- When `TYPESAFE_API_KEY` is present and the `[jev]` extra is installed, Jev
  classifies/routes/risk-tasks at Intake; low-confidence or abstained
  decisions escalate to the human.
- Without the key, a **deterministic heuristic classifier** runs (marker
  files, path patterns, keyword rules) — the pipeline never blocks on Jev.
- Jev output is **advisory**: it proposes, the pipeline + gates decide. No
  gate trusts a model-produced classification as verification.

## Consequences

- `[jev]` is an optional dependency extra; `jev`/`typesafe-sdk` never become
  hard requirements.
- The heuristic fallback must be good enough that the factory works fully
  offline (it is the default).
- Semantic parity between Jev and fallback outputs is maintained via a
  shared pydantic `IntakeDecision` model.
