"""In-runtime budget guard: read month-to-date spend via the readonly SP and act.

Azure analog of aws_demo/budget_guard.py. The *authoritative* backstop is out-of-band (an Azure
Consumption budget + action group that survives an unhealthy runtime — the analog of the AWS Budgets
alarm). This is the first line of defence: it reads MTD spend via Azure Cost Management and, on
TEARDOWN, POSTs a governed teardown mission to the Mission Runtime rather than mutating anything.
"""
from __future__ import annotations

import json
import os
import urllib.request

from demo_common.budget import BudgetAction, BudgetPolicy

from .creds import AzureDemoCreds


def month_to_date_spend(session) -> float:
    """MonthToDate actual cost via Azure Cost Management (readonly SP).

    ``session`` is an ``AzureSession`` (``.credential`` + ``.subscription_id``). The SDK import is
    guarded so this module imports without azure-mgmt-costmanagement installed.
    """
    from azure.mgmt.costmanagement import CostManagementClient
    from azure.mgmt.costmanagement.models import (
        QueryAggregation,
        QueryDataset,
        QueryDefinition,
    )

    client = CostManagementClient(session.credential)
    scope = f"/subscriptions/{session.subscription_id}"
    query = QueryDefinition(
        type="ActualCost",
        timeframe="MonthToDate",
        dataset=QueryDataset(
            granularity="None",
            aggregation={"totalCost": QueryAggregation(name="Cost", function="Sum")},
        ),
    )
    result = client.query.usage(scope=scope, parameters=query)
    rows = getattr(result, "rows", None) or []
    if not rows:
        return 0.0
    # the Cost column is the first aggregation in the row (matching the query's totalCost)
    return float(rows[0][0])


def _open_teardown_mission(mission_api: str) -> None:
    body = json.dumps(
        {"goal": "Budget cap reached — tear down the demo Azure deployment", "template": "teardown_app"}
    ).encode()
    req = urllib.request.Request(
        mission_api.rstrip("/") + "/missions",
        data=body,
        headers={"content-type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=15).read()


def check_and_act(
    cap_usd: float | None = None,
    creds: AzureDemoCreds | None = None,
    mission_api: str | None = None,
) -> tuple[BudgetAction, float]:
    cap_usd = cap_usd if cap_usd is not None else float(os.environ.get("DEMO_BUDGET_USD", "100"))
    creds = creds or AzureDemoCreds()
    mission_api = mission_api or os.environ.get("MISSION_API_URL", "http://localhost:8080")

    spend = month_to_date_spend(creds.session("readonly"))
    action = BudgetPolicy(cap_usd).evaluate(spend)
    if action is BudgetAction.TEARDOWN:
        _open_teardown_mission(mission_api)  # governed; the out-of-band budget alarm is the real backstop
    return action, spend


if __name__ == "__main__":  # pragma: no cover
    action, spend = check_and_act()
    print(f"budget-guard: MTD ${spend:.2f} -> {action.value}")
