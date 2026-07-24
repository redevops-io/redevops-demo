"""In-runtime budget guard for the DO demo: read month-to-date spend via the DO Billing API and act.

DO analog of aws_demo/budget_guard.py. AWS reads Cost Explorer; DO exposes month-to-date usage on the
customer balance endpoint (GET /v2/customers/my/balance → month_to_date_usage). Same governed teardown
POST shape as AWS: on cap we open a teardown mission on the Mission Runtime rather than mutating anything
directly.
"""
from __future__ import annotations

import json
import os
import urllib.request

from demo_common.budget import BudgetAction, BudgetPolicy

from .creds import DoDemoCreds


def month_to_date_spend(client) -> float:
    """Month-to-date usage (USD) via the DO Billing API.

    GET /v2/customers/my/balance returns e.g.:
        {"month_to_date_balance": "23.44", "account_balance": "12.23",
         "month_to_date_usage": "23.44", "generated_at": "..."}
    We use month_to_date_usage (spend accrued this month); fall back to month_to_date_balance.
    """
    bal = client.get("/v2/customers/my/balance")
    val = bal.get("month_to_date_usage") or bal.get("month_to_date_balance") or "0"
    return float(val)


def _open_teardown_mission(mission_api: str) -> None:
    body = json.dumps(
        {"goal": "Budget cap reached — tear down the demo DigitalOcean deployment", "template": "teardown_app"}
    ).encode()
    req = urllib.request.Request(
        mission_api.rstrip("/") + "/missions",
        data=body,
        headers={"content-type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=15).read()


def check_and_act(
    cap_usd: float | None = None,
    creds: DoDemoCreds | None = None,
    mission_api: str | None = None,
) -> tuple[BudgetAction, float]:
    cap_usd = cap_usd if cap_usd is not None else float(os.environ.get("DEMO_BUDGET_USD", "100"))
    creds = creds or DoDemoCreds()
    mission_api = mission_api or os.environ.get("MISSION_API_URL", "http://localhost:8080")

    spend = month_to_date_spend(creds.session("readonly"))
    action = BudgetPolicy(cap_usd).evaluate(spend)
    if action is BudgetAction.TEARDOWN:
        _open_teardown_mission(mission_api)  # governed; an out-of-band billing alert is the real backstop
    return action, spend


if __name__ == "__main__":  # pragma: no cover
    action, spend = check_and_act()
    print(f"budget-guard: MTD ${spend:.2f} -> {action.value}")
