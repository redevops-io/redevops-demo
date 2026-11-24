"""Wiring proof: the real (agentic-os) infra operator drives THIS repo's terraform env.

Uses the operator's injectable runner, so no terraform/AWS is actually invoked ($0). We
assert the exact command it would run points at infra/terraform/envs/aws.
"""
import importlib
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]


def test_plan_targets_our_env(infra_core, monkeypatch):
    monkeypatch.setenv("INFRA_DEPLOY_ROOT", str(REPO / "infra"))
    core = importlib.reload(infra_core)  # DEPLOY_ROOT is read at import → reload to pick up env

    calls = []

    def fake_run(argv, cwd=None):
        calls.append(argv)
        return (0, "", "")

    core.terraform_plan("aws", {"region": "us-east-1"}, run=fake_run)
    cmd = " ".join(calls[0])
    assert f"-chdir={REPO}/infra/terraform/envs/aws" in cmd
    assert "plan" in calls[0]
    assert "region=us-east-1" in cmd


def test_destroy_targets_our_env(infra_core, monkeypatch):
    """The kernel destroys ONLY what the forward apply recorded: it plans a -target destroy in OUR env directory, checks the plan touches nothing
    else, then applies that saved plan. With nothing recorded it refuses and runs nothing."""
    import json

    monkeypatch.setenv("INFRA_DEPLOY_ROOT", str(REPO / "infra"))
    core = importlib.reload(infra_core)
    created = ["aws_instance.web"]
    calls = []

    def fake_run(argv, cwd=None):
        calls.append(argv)
        if "show" in argv:
            return (0, json.dumps({"resource_changes": [{"address": a, "change": {"actions": ["delete"]}} for a in created]}), "")
        return (0, "", "")

    core.terraform_destroy("aws", created, run=fake_run)
    plan = " ".join(calls[0])
    assert f"-chdir={REPO}/infra/terraform/envs/aws" in plan and "-destroy" in calls[0] and "-target=aws_instance.web" in calls[0]
    assert all(f"-chdir={REPO}/infra/terraform/envs/aws" in " ".join(c) for c in calls), "every terraform call stays in our env directory"
    assert any("apply" in c for c in calls), "the verified plan is applied"

    calls.clear()
    try:
        out = core.terraform_destroy("aws", run=fake_run)
    except Exception as exc:  # noqa: BLE001 - the kernel refuses (older kernels by raising, the operator wrapper by an error result)
        out = {"error": str(exc)}
    assert calls == [] and "refus" in json.dumps(out).lower(), "nothing recorded: nothing is destroyed"

