"""In-runtime budget policy for demo missions (cloud-agnostic core).

The *authoritative* safety net is out-of-band (an AWS Budgets alarm + emergency-destroy
that survives an unhealthy runtime — see infra/terraform/safety/). This module is the
in-runtime guard: given current spend it decides whether a mission should proceed, warn,
or trigger a governed teardown.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class BudgetAction(str, Enum):
    OK = "ok"          # under warn threshold — proceed
    WARN = "warn"      # over warn threshold — surface in the cockpit, keep running
    TEARDOWN = "teardown"  # at/over cap — open a governed teardown mission


@dataclass(frozen=True)
class BudgetPolicy:
    cap_usd: float
    warn_fraction: float = 0.8

    def __post_init__(self) -> None:
        if self.cap_usd <= 0:
            raise ValueError("cap_usd must be > 0")
        if not 0 < self.warn_fraction < 1:
            raise ValueError("warn_fraction must be in (0, 1)")

    def evaluate(self, spend_usd: float) -> BudgetAction:
        if spend_usd < 0:
            raise ValueError("spend_usd must be >= 0")
        if spend_usd >= self.cap_usd:
            return BudgetAction.TEARDOWN
        if spend_usd >= self.cap_usd * self.warn_fraction:
            return BudgetAction.WARN
        return BudgetAction.OK

    def remaining_usd(self, spend_usd: float) -> float:
        return max(0.0, self.cap_usd - spend_usd)
