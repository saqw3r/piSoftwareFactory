"""piSoftwareFactory — a local-first AI software factory.

Bootstrapped into any project with ``sfactory init``, it runs an 8-stage
pipeline (Intake → Brief → Dispatch → Execute → Gates → Review → Release →
Reflect) on headless pi workers served by the owner's llama.cpp, with
deterministic verification gates, Hindsight memory, and Paperclip governance.

Architecture decisions live in ``decisions/`` (ADR-0001..).
"""

from __future__ import annotations

__version__ = "0.1.2"

__all__ = ["__version__"]
