# ADR-0020: Typed intake — local model, not hosted Jev

- **Status:** Proposed (research complete; `provider = "local"` implementation is the follow-up)
- **Date:** 2026-11-05
- **Deciders:** Serhii Surnin (owner), ZCode Muse (research)
- **Origin:** owner request — "next research task will be add jev. What we will benefit with it?"
- **Evidence:** `research/typed-intake/` (harness + measured table, rerun on every candidate brain)

## Context

ADR-0004 made Jev the optional advisory intake with a heuristic fallback, on a
sketch of its API (typed decisions + confidence + abstention). The research
verified the real package against that sketch and measured the alternatives on
14 tasks (4 real from the zen graph, 10 probes targeting known
misclassification classes — including the "documenting"-morphology miss that
classified a docs task as a feature in a live run).

## Findings

- **jev 0.3.0 does not match the ADR-0004 sketch.** PyPI-verified: the package
  is hosted-only (`TYPESAFE_API_KEY`, console.typesafe.ai), requires
  **Python >= 3.14** (the factory's tool venv is 3.11), and — decisively —
  *"probabilities are discarded"*: **no confidence and no abstention reach the
  caller**. The two benefits that motivated ADR-0004 are unavailable through
  `jev.fn`. Single maintainer, no license stated.
- **The local model delivers the shape instead.** Asked for strict JSON with a
  confidence field (abstain < 0.6 → heuristic fallback, the existing
  `run_intake` rule), Nemotron-3.5-Bolt E4B scored **10/14 expected-label hits
  with 2/2 correct abstentions**, vs the heuristic's **7/14 with fabricated
  kinds on both vague tasks**. The local leg fixed both real-task misses,
  including the morphology one.
- Honest weaknesses: one overconfident miss ("port the exporter" → chore at
  1.0 confidence), and one real task abstained where a label existed (a
  typo'd, genuinely vague task — escalation, not misrouting).

## Decision

1. **Skip hosted Jev.** It conflicts with local-first (ADR-0001), needs a
   Python the factory does not ship, and cannot deliver the confidence and
   abstention that motivated ADR-0004.
2. **Add `[intake] provider = "local"`**: the local endpoint asked for the
   strict JSON shape proven in `research/typed-intake/compare_intake.py` —
   same `IntakeDecision` contract, `confidence < 0.6` → abstain → heuristic
   fallback. `auto` prefers local when an endpoint answers, heuristic offline.
3. **Keep the heuristic** as the offline default and abstention backstop.
4. Re-benchmark on every brain change with the harness; the numbers are a
   property of the model, not of the design.

## Consequences

- Intake quality stops being regex-shaped: morphology, multi-clause tasks and
  vagueness are handled; vague tasks escalate instead of misrouting.
- One extra short LLM call per task at Intake (seconds on the local router);
  offline → heuristic, unchanged.
- The benchmark (tasks + harness) is committed and is the acceptance test for
  the implementation and for future model swaps.
