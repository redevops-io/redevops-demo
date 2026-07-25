"""In-runtime budget guard for the GCP demo: read month-to-date spend and act.

This is the *first* line of defence (the authoritative one is the out-of-band Cloud Billing budget
alarm in infra/terraform/safety/). On TEARDOWN it POSTs a governed teardown mission to the Mission
Runtime rather than mutating anything directly — the same governed-teardown shape as the AWS guard.

GCP cost caveat: there is no cheap synchronous "month-to-date spend" API. Accurate MTD requires the
BigQuery **billing export** (heavy to stand up). So:
  • if a billing-export BigQuery table is configured (env DEMO_GCP_BILLING_BQ_TABLE), we sum MTD
    cost from it and evaluate the same BudgetPolicy the AWS guard uses;
  • otherwise we return a clearly-labelled WARN — spend is UNKNOWN, not zero — and do NOT tear down
    on a number we don't have. The out-of-band Billing budget alarm remains the real backstop.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import urllib.request
from typing import Optional

from demo_common.budget import BudgetAction, BudgetPolicy

from .creds import GcpDemoCreds


def month_to_date_spend(creds: GcpDemoCreds, *, bq_table: Optional[str] = None) -> Optional[float]:
    """MTD cost (USD) from the BigQuery billing export, or ``None`` when it isn't configured.

    ``bq_table`` is a fully-qualified ``project.dataset.table`` billing-export table. Returns
    ``None`` (spend UNKNOWN) if unset — GCP has no cheap synchronous MTD spend API to fall back on.
    """
    bq_table = bq_table or os.environ.get("DEMO_GCP_BILLING_BQ_TABLE")
    if not bq_table:
        return None

    # lazy import: package must import without google-cloud-bigquery present
    from google.cloud import bigquery  # type: ignore

    client = bigquery.Client(credentials=creds.credentials("readonly"), project=creds.project_id)
    start = _dt.date.today().replace(day=1).isoformat()
    # cost + credits give net spend, matching how the billing export models it
    query = f"""
        SELECT COALESCE(SUM(cost), 0)
             + COALESCE(SUM((SELECT SUM(c.amount) FROM UNNEST(credits) c)), 0) AS mtd
        FROM `{bq_table}`
        WHERE DATE(usage_start_time) >= DATE('{start}')
    """
    row = next(iter(client.query(query).result()), None)
    return float(row["mtd"]) if row and row["mtd"] is not None else 0.0


def _open_teardown_mission(mission_api: str) -> None:
    body = json.dumps(
        {"goal": "Budget cap reached — tear down the demo GCP deployment", "template": "teardown_app"}
    ).encode()
    req = urllib.request.Request(
        mission_api.rstrip("/") + "/missions",
        data=body,
        headers={"content-type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=15).read()


def check_and_act(
    cap_usd: float | None = None,
    creds: GcpDemoCreds | None = None,
    mission_api: str | None = None,
) -> tuple[BudgetAction, Optional[float]]:
    """Return (action, spend). ``spend`` is ``None`` when MTD is UNKNOWN (no billing export); in that
    case the action is WARN and no teardown is opened — we never tear down on a number we don't have.
    """
    cap_usd = cap_usd if cap_usd is not None else float(os.environ.get("DEMO_BUDGET_USD", "100"))
    creds = creds or GcpDemoCreds()
    mission_api = mission_api or os.environ.get("MISSION_API_URL", "http://localhost:8080")

    spend = month_to_date_spend(creds)
    if spend is None:
        # UNKNOWN spend — surface it, keep running, let the out-of-band Billing budget alarm backstop.
        return BudgetAction.WARN, None

    action = BudgetPolicy(cap_usd).evaluate(spend)
    if action is BudgetAction.TEARDOWN:
        _open_teardown_mission(mission_api)  # governed; the out-of-band alarm is the real backstop
    return action, spend


if __name__ == "__main__":  # pragma: no cover
    action, spend = check_and_act()
    if spend is None:
        print(f"budget-guard: MTD UNKNOWN (configure DEMO_GCP_BILLING_BQ_TABLE) -> {action.value}")
    else:
        print(f"budget-guard: MTD ${spend:.2f} -> {action.value}")
