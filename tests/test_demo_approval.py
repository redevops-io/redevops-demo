"""Isolated-demo auto-approval: DemoApprovalDriver fails closed unless EVERYTHING lines up, and it approves only
through the kernel's normal approve path (the kernel's own plan_digest / token gates still run)."""
import dataclasses
import json
import pathlib
import smtplib
import socket

import pytest

import missions.deploy_operate as dop
from agentic_os.mission.types import MissionState
from demo_common import demo_approval as da
from demo_common.demo_approval import (
    ACTOR, AllowEntry, DemoApprovalDriver, DemoApprovalRefused, DemoPolicy, PolicyError)

T0 = 1_800_000_000.0
TENANT, ENVNAME = "demo-sandbox-tenant", "demo-isolated-sim"
REPO = pathlib.Path(__file__).resolve().parents[1]


def _policy(*caps, expires=T0 + 86400, tenant=TENANT, environment=ENVNAME):
    return DemoPolicy(version="test-1", allow=tuple(AllowEntry(c) for c in (caps or (
        "infra.provision", "infra.configure"))), tenant=tenant, environment=environment, expires_at=expires)


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t


@pytest.fixture(autouse=True)
def no_ambient_creds(monkeypatch):
    for k in da._REAL_CREDENTIAL_ENV:
        monkeypatch.delenv(k, raising=False)


@pytest.fixture(autouse=True)
def canary(monkeypatch):
    """No real network / SMTP, ever: any attempt is recorded and raises."""
    attempts = []

    def boom(name):
        def f(*a, **k):
            attempts.append(name)
            raise AssertionError(f"network attempt blocked: {name}")
        return f
    monkeypatch.setattr(socket.socket, "connect", boom("socket.connect"))
    monkeypatch.setattr(socket, "create_connection", boom("create_connection"))
    monkeypatch.setattr(socket, "getaddrinfo", boom("getaddrinfo"))
    monkeypatch.setattr(smtplib.SMTP, "connect", boom("smtp.connect"))
    monkeypatch.setattr(smtplib.SMTP_SSL, "connect", boom("smtp_ssl.connect"))
    return attempts


@pytest.fixture
def env():
    return {da.ENV_MODE: "isolated", da.ENV_TENANT: TENANT, da.ENV_ENVIRONMENT: ENVNAME}


@pytest.fixture
def clock():
    return Clock()


def mission(tenant=TENANT, rt=None):
    rt = rt or dop.build_runtime(sim=True)
    m = dop.create_deploy_mission(rt, tenant=tenant)
    assert m.state == MissionState.WAITING_HUMAN
    return rt, m


def driver(rt, env, clock, tmp_path, policy=None, **kw):
    return DemoApprovalDriver(rt, policy or _policy(), env=env, receipts_path=str(tmp_path / "r.jsonl"),
                              clock=clock, **kw)


def refused(code, fn, *a):
    with pytest.raises(DemoApprovalRefused) as ei:
        fn(*a)
    assert ei.value.code == code, ei.value
    return ei.value


def parked_at(rt, m, capability):
    assert m.state == MissionState.WAITING_HUMAN
    assert rt.repo.pending_human(m.id)["capability"] == capability


# ---------------------------------------------------------------- happy path + audit
def test_drives_both_gates_with_receipts(env, clock, tmp_path):
    rt, m = mission()
    drv = driver(rt, env, clock, tmp_path)
    receipts = drv.drive(m.id)
    assert m.state == MissionState.SUCCEEDED
    assert [r["capability"] for r in receipts] == ["infra.provision", "infra.configure"]
    for r in receipts:
        assert r["actor"] == ACTOR == "demo_driver" and r["policy_version"] == "test-1"
        assert r["decision"] == "approve" and len(r["digest"]) == 64 and r["ts"] == T0
    # receipts in the mission event log, written BEFORE the kernel's ApprovalGranted
    log = [e for e in rt.repo.timeline(m.id) if e["type"] in (da.EVENT_TYPE, "ApprovalGranted")]
    assert [e["type"] for e in log] == [da.EVENT_TYPE, "ApprovalGranted"] * 2
    assert log[0]["payload"]["digest"] == receipts[0]["digest"]
    # ... and in the JSONL file
    lines = [json.loads(x) for x in (tmp_path / "r.jsonl").read_text().splitlines()]
    assert [x["digest"] for x in lines] == [r["digest"] for r in receipts]


def test_goes_through_kernel_approve_with_plan_digest(env, clock, tmp_path):
    rt, m = mission()
    seen = []

    def spy(mid, node, decision, edit=None):
        seen.append((node, decision, edit))
        return rt.approve(mid, node, decision, edit=edit)
    driver(rt, env, clock, tmp_path, approve_fn=spy).drive(m.id)
    assert m.state == MissionState.SUCCEEDED
    assert seen[0][1] == "approve" and seen[0][2] == {"plan_digest": dop.plan_digest_of(rt, m.id)} != {}
    assert seen[1][2] is None   # configure: plain approve, exactly what a human sends


def test_action_digest_is_sha256_of_canonical_json():
    import hashlib
    d = da.action_digest("m1", "n1", "a.b", "t", "e", {"b": 1, "a": 2})
    want = hashlib.sha256(
        b'{"capability":"a.b","environment":"e","inputs":{"a":2,"b":1},"mission_id":"m1","node_id":"n1","tenant":"t"}'
    ).hexdigest()
    assert d == want


# ---------------------------------------------------------------- disabled by default / fail closed
def test_disabled_by_default(clock, tmp_path):
    rt, m = mission()
    drv = driver(rt, {}, clock, tmp_path)
    assert not drv.enabled and "REDEVOPS_DEMO_MODE" in drv.disabled_reason()
    refused("disabled", drv.request, m.id)
    assert drv.drive(m.id)[0]["decision"] == "refused"
    parked_at(rt, m, "infra.provision")
    # the refusal itself is logged
    assert any(e["type"] == da.EVENT_TYPE and e["payload"]["decision"] == "refused" for e in rt.repo.timeline(m.id))


def test_default_environment_has_no_policy_and_is_disabled(monkeypatch, tmp_path):
    monkeypatch.delenv(da.ENV_MODE, raising=False)
    monkeypatch.delenv(da.ENV_ALLOWLIST, raising=False)
    rt, m = mission()
    assert DemoApprovalDriver(rt, receipts_path=str(tmp_path / "r")).enabled is False


@pytest.mark.parametrize("mode", ["", "1", "true", "isolated ", "ISOLATED", "production"])
def test_only_exact_isolated_mode_enables(mode, env, clock, tmp_path):
    env[da.ENV_MODE] = mode
    rt, m = mission()
    refused("disabled", driver(rt, env, clock, tmp_path).request, m.id)


def test_missing_allowlist_file_is_disabled(env, clock, tmp_path):
    env[da.ENV_ALLOWLIST] = str(tmp_path / "nope.json")
    rt, m = mission()
    drv = DemoApprovalDriver(rt, env=env, receipts_path=str(tmp_path / "r"), clock=clock)
    assert not drv.enabled and "allowlist" in drv.disabled_reason()


def test_committed_allowlist_loads_and_enables(env, clock, tmp_path):
    env[da.ENV_ALLOWLIST] = str(REPO / "deploy" / "demo-allowlist.json")
    rt, m = mission()
    drv = DemoApprovalDriver(rt, env=env, receipts_path=str(tmp_path / "r"), clock=clock)
    drv.clock = clock
    clock.t = 1_790_000_000.0   # before the file's expiry
    assert drv.enabled
    drv.drive(m.id)
    assert m.state == MissionState.SUCCEEDED


def test_non_simulated_runtime_refused(env, clock, tmp_path):
    rt, m = mission()
    rt.demo_simulated = False
    refused("disabled", driver(rt, env, clock, tmp_path).request, m.id)


def test_real_credentials_in_env_refused(env, clock, tmp_path):
    env["AWS_ACCESS_KEY_ID"] = "x"
    rt, m = mission()
    e = refused("disabled", driver(rt, env, clock, tmp_path).request, m.id)
    assert "AWS_ACCESS_KEY_ID" in e.reason and "x" not in e.reason.replace("AWS_ACCESS_KEY_ID", "").replace(" ", "")[:0]


# ---------------------------------------------------------------- ACCEPTANCE: tenant / digest / environment
def test_changing_target_tenant_fails_mission_tenant(env, clock, tmp_path):
    rt, m = mission(tenant="acme-production")
    drv = driver(rt, env, clock, tmp_path)
    refused("target_mismatch", lambda: drv.approve(m.id, drv.request(m.id)))
    parked_at(rt, m, "infra.provision")


def test_changing_target_tenant_fails_ambient_marker(env, clock, tmp_path):
    rt, m = mission()
    drv = driver(rt, env, clock, tmp_path)
    req = drv.request(m.id)
    env[da.ENV_TENANT] = "someone-elses-tenant"
    refused("disabled", drv.approve, m.id, req)
    parked_at(rt, m, "infra.provision")


def test_changing_target_tenant_fails_in_request(env, clock, tmp_path):
    rt, m = mission()
    drv = driver(rt, env, clock, tmp_path)
    req = drv.request(m.id)
    bad = dataclasses.replace(req, tenant="acme-production")
    refused("target_mismatch", drv.approve, m.id, bad)
    parked_at(rt, m, "infra.provision")


def test_changing_action_digest_fails(env, clock, tmp_path):
    rt, m = mission()
    drv = driver(rt, env, clock, tmp_path)
    req = drv.request(m.id)
    refused("digest_mismatch", drv.approve, m.id, dataclasses.replace(req, digest="0" * 64))
    refused("digest_mismatch", drv.approve, m.id, dataclasses.replace(req, digest=req.digest[:-1] + "x"))
    parked_at(rt, m, "infra.provision")
    drv.approve(m.id, req)   # the genuine, untouched request still works
    parked_at(rt, m, "infra.configure")


def test_changing_environment_fails_ambient_marker(env, clock, tmp_path):
    rt, m = mission()
    drv = driver(rt, env, clock, tmp_path)
    req = drv.request(m.id)
    env[da.ENV_ENVIRONMENT] = "production"
    refused("disabled", drv.approve, m.id, req)
    parked_at(rt, m, "infra.provision")


def test_changing_environment_fails_in_request(env, clock, tmp_path):
    rt, m = mission()
    drv = driver(rt, env, clock, tmp_path)
    req = drv.request(m.id)
    refused("target_mismatch", drv.approve, m.id, dataclasses.replace(req, environment="production"))
    parked_at(rt, m, "infra.provision")


def test_environment_marker_wrong_from_the_start(env, clock, tmp_path):
    env[da.ENV_ENVIRONMENT] = "staging"
    rt, m = mission()
    refused("disabled", driver(rt, env, clock, tmp_path).request, m.id)


# ---------------------------------------------------------------- expired / replayed / cross-mission / modified
def test_expired_request_fails(env, clock, tmp_path):
    rt, m = mission()
    drv = driver(rt, env, clock, tmp_path, ttl_seconds=60)
    req = drv.request(m.id)
    clock.t += 61
    refused("expired", drv.approve, m.id, req)
    parked_at(rt, m, "infra.provision")


def test_expired_policy_fails_closed(env, clock, tmp_path):
    rt, m = mission()
    drv = driver(rt, env, clock, tmp_path, policy=_policy(expires=T0 + 10))
    req = drv.request(m.id)
    clock.t += 11
    refused("disabled", drv.approve, m.id, req)


def test_replayed_request_fails(env, clock, tmp_path):
    rt, m = mission()
    drv = driver(rt, env, clock, tmp_path)
    req = drv.request(m.id)
    drv.approve(m.id, req)
    parked_at(rt, m, "infra.configure")
    refused("replayed", drv.approve, m.id, req)
    # a brand-new driver (restart) still knows: one-time use survives via receipts
    drv2 = driver(rt, env, clock, tmp_path)
    refused("replayed", drv2.approve, m.id, req)
    # ... and so does one with a fresh receipt file, via the mission's own event log
    drv3 = DemoApprovalDriver(rt, _policy(), env=env, receipts_path=str(tmp_path / "other.jsonl"), clock=clock)
    refused("replayed", drv3.approve, m.id, req)
    parked_at(rt, m, "infra.configure")


def test_cross_mission_request_fails(env, clock, tmp_path):
    rt, a = mission()
    _, b = mission(rt=rt)
    drv = driver(rt, env, clock, tmp_path)
    req_a = drv.request(a.id)
    refused("cross_mission", drv.approve, b.id, req_a)
    parked_at(rt, b, "infra.provision")
    parked_at(rt, a, "infra.provision")
    # forging the mission id inside the request does not help: it no longer matches the live gate
    forged = dataclasses.replace(req_a, mission_id=b.id)
    refused("gate_mismatch", drv.approve, b.id, forged)
    parked_at(rt, b, "infra.provision")


def test_modified_inputs_fail(env, clock, tmp_path):
    rt, m = mission()
    drv = driver(rt, env, clock, tmp_path)
    req = drv.request(m.id)
    inputs = dict(req.inputs, extra="rm -rf /")
    # even a self-consistent forgery (digest recomputed over the altered inputs) is checked against the live gate
    forged = dataclasses.replace(req, inputs=inputs, digest=da.action_digest(
        req.mission_id, req.node_id, req.capability, req.tenant, req.environment, inputs))
    refused("digest_mismatch", drv.approve, m.id, forged)
    # altered inputs with the original digest
    refused("digest_mismatch", drv.approve, m.id, dataclasses.replace(req, inputs=inputs))
    parked_at(rt, m, "infra.provision")


def test_request_for_earlier_gate_cannot_approve_later_gate(env, clock, tmp_path):
    rt, m = mission()
    drv = driver(rt, env, clock, tmp_path)
    req = drv.request(m.id)
    drv.approve(m.id, req)
    parked_at(rt, m, "infra.configure")
    refused("replayed", drv.approve, m.id, req)


# ---------------------------------------------------------------- allowlist
def test_not_in_allowlist_stays_parked(env, clock, tmp_path):
    rt, m = mission()
    drv = driver(rt, env, clock, tmp_path, policy=_policy("infra.configure"))
    refused("not_allowlisted", lambda: drv.approve(m.id, drv.request(m.id)))
    parked_at(rt, m, "infra.provision")
    assert drv.drive(m.id)[-1]["code"] == "not_allowlisted"


def test_params_shape_enforced(env, clock, tmp_path):
    rt, m = mission()
    pol = DemoPolicy("t", (AllowEntry("infra.provision", params={"something_else": None}),
                           AllowEntry("infra.configure")), TENANT, ENVNAME, T0 + 99)
    drv = driver(rt, env, clock, tmp_path, policy=pol)
    refused("params_not_allowed", lambda: drv.approve(m.id, drv.request(m.id)))
    assert AllowEntry("x.y", max_cost_usd=5).permits({"est_cost_usd": 9}) is not None
    assert AllowEntry("x.y", max_cost_usd=5).permits({"est_cost_usd": 4, }) is not None   # no params: literal rejected
    assert AllowEntry("x.y", params={"est_cost_usd": None}, max_cost_usd=5).permits({"est_cost_usd": 4}) is None


@pytest.mark.parametrize("cap", ["*", "infra.*", "infra.provision*", "", "all", "infra.prov?sion", "[a-z].b", "infra"])
def test_wildcard_allowlist_rejected(cap):
    with pytest.raises(PolicyError):
        AllowEntry(cap)
    with pytest.raises(PolicyError):
        DemoPolicy.from_dict({"version": "v", "tenant": TENANT, "environment": ENVNAME,
                              "expires_at": "2030-01-01T00:00:00Z", "allow": [{"capability": cap}]})


def test_wildcard_param_value_and_empty_or_prod_policy_rejected():
    with pytest.raises(PolicyError):
        AllowEntry("a.b", params={"k": ["*"]})
    with pytest.raises(PolicyError):
        DemoPolicy("v", (), TENANT, ENVNAME, T0)
    with pytest.raises(PolicyError):
        _policy(tenant="acme-production")
    with pytest.raises(PolicyError):
        _policy(environment="demo-prod")
    with pytest.raises(PolicyError):
        DemoPolicy.from_dict({"version": "v", "tenant": TENANT, "environment": ENVNAME, "expires_at": "2030-01-01",
                              "allow": [{"capability": "a.b", "wildcard": True}]})


# ---------------------------------------------------------------- no real side effects + CLI
def test_canary_no_network_or_smtp(env, clock, tmp_path, canary):
    rt, m = mission()
    driver(rt, env, clock, tmp_path).drive(m.id)
    assert m.state == MissionState.SUCCEEDED
    assert canary == []
    # and the guard itself works
    with pytest.raises(AssertionError):
        socket.create_connection(("example.com", 25))
    assert canary == ["create_connection"]


def test_cli_banner_only_when_enabled(env, clock, tmp_path):
    rt, m = mission()
    out = []
    da.run_auto_approve(rt, m.id, out=out.append, driver=driver(rt, {}, clock, tmp_path))
    assert not any(da.BANNER in x for x in out) and "NOT enabled" in out[0]
    parked_at(rt, m, "infra.provision")
    out.clear()
    da.run_auto_approve(rt, m.id, out=out.append, driver=driver(rt, env, clock, tmp_path))
    assert out[0] == "ISOLATED DEMO: auto-approving allowlisted simulated actions"
    assert m.state == MissionState.SUCCEEDED


def test_deploy_main_default_does_not_auto_approve(monkeypatch, capsys, tmp_path):
    for k in (da.ENV_MODE, da.ENV_TENANT, da.ENV_ENVIRONMENT, da.ENV_ALLOWLIST, "APPROVE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)
    dop.main(["--auto-approve"])
    out = capsys.readouterr().out
    assert "auto-approval NOT enabled" in out and da.BANNER not in out
