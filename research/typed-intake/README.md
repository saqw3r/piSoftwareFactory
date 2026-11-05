# Typed intake decisions: hosted Jev vs local model vs heuristic (2026-11-05)

Research task from the owner: what would adding **Jev** (ADR-0004) actually buy?
Measured on 14 tasks (4 real from the zen graph + 10 probes targeting known
misclassification classes, incl. the "documenting"-morphology miss and two
should-abstain vague tasks). Rerun anytime:

```powershell
uv run --project F:\SoftwareFactory python research/typed-intake/compare_intake.py
```

## Verified facts about jev 0.3.0 (PyPI, 2026-11-05)

- **Hosted-only** — requires `TYPESAFE_API_KEY` (console.typesafe.ai); no offline mode.
- **Requires Python >= 3.14** — the factory's tool venv is 3.11.
- **"Probabilities are discarded"** — no confidence and no abstention reach the
  caller. ADR-0004's sketch (confidence + `abstained: true`) does not match the
  real package: its headline benefits are unavailable through `jev.fn`.
- Single maintainer, no license stated, 14 kB wheel — vetting burden for a
  decision-critical path.

## Measured results (Nemotron-3.5-Bolt E4B @ llama-router, temp 0)

```
heuristic: 7/14 expected-label hits, 0 abstentions (fabricates "feature" on vague tasks)
local:     10/14 expected-label hits, 2/2 correct abstentions (0.3, 0.0 confidence)
jev:       skipped — package missing, key missing, python < 3.14 (design-only leg)
```

- The local leg fixed **both** real-task misses the heuristic made
  (C4-report → docs, Development-section → docs — the morphology miss).
- It abstained honestly on both vague tasks instead of fabricating a kind.
- Its 3 misses: "rotate leaked token" → bug (defensible; expected chore),
  "unit tests" → chore, "port exporter" → chore at confidence 1.0 — the
  overconfident miss is the failure mode to watch at scale.

## Recommendation (towards ADR-0020)

1. **Skip hosted Jev.** It conflicts with local-first (ADR-0001), needs
   Python 3.14, and does not deliver the confidence/abstention that motivated
   ADR-0004 in the first place.
2. **Add `provider = "local"` typed intake**: the local endpoint asked for the
   strict JSON shape used by `run_local()` here — same `IntakeDecision`
   contract, `confidence < 0.6` → abstain → heuristic fallback (the existing
   `run_intake` rule, unchanged).
3. **Keep the heuristic** as offline fallback and abstention backstop.
4. Re-benchmark when the factory brain changes (this run used the
   Nemotron-3.5-Bolt E4B; a stronger model should shrink the overconfident
   misses).

Caveats: one model, one temperature, small N, labels are the author's judgment
(disclosed per task in tasks.jsonl). The harness, not the numbers, is the
deliverable — rerun it on every candidate brain.
