"""Isolated-demo auto-approval: DemoApprovalDriver fails closed unless EVERYTHING lines up, and it approves only
through the kernel's normal approve path (the kernel's own plan_digest / token gates still run)."""
import dataclasses
import hashlib
import hmac
import json
import multiprocessing
import pathlib
import smtplib
import socket
import threading

import pytest

import missions.deploy_operate as dop
from agentic_os.mission.types import MissionState
from demo_common import demo_approval as da
from demo_common.demo_approval import (
    ACTOR, AllowEntry, DemoApprovalDriver, DemoApprovalRefused, DemoPolicy, PolicyError)

T0 = 1_800_000_000.0
TENANT, ENVNAME = "demo-sandbox", "isolated-demo"
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
    kw.setdefault("home", str(tmp_path / "home"))   # a clean HOME: never the developer's real ~/.aws etc.
    return DemoApprovalDriver(rt, policy or _policy(), env=env, receipts_path=str(tmp_path / "r.jsonl"),
                              clock=clock, **kw)


def signed(drv, req, **changes):
    """A modified request RE-AUTHENTICATED with the process key: models an attacker who defeats the MAC, so the
    inner checks (digest vs live gate, tenant, allowlist, ...) are tested on their own."""
    return drv._sign(dataclasses.replace(req, **changes))


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
    log = [e for e in rt.repo.timeline(m.id) if e["type"] in (da.INTENT_EVENT, da.RESULT_EVENT, "ApprovalGranted")]
    assert [e["type"] for e in log] == [da.INTENT_EVENT, "ApprovalGranted", da.RESULT_EVENT] * 2
    assert log[0]["payload"]["digest"] == receipts[0]["digest"]
    assert log[2]["payload"]["outcome"] == "granted"
    # ... and in the JSONL file
    lines = [json.loads(x) for x in (tmp_path / "r.jsonl").read_text().splitlines()]
    assert [x["digest"] for x in lines if x["phase"] == "result"] == [r["digest"] for r in receipts]
    assert [x["phase"] for x in lines] == ["intent", "result"] * 2


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
    drv = DemoApprovalDriver(rt, env=env, receipts_path=str(tmp_path / "r"), clock=clock, home=str(tmp_path))
    assert not drv.enabled and "allowlist" in drv.disabled_reason()


def test_committed_allowlist_loads_and_enables(env, clock, tmp_path):
    env[da.ENV_ALLOWLIST] = str(REPO / "deploy" / "demo-allowlist.json")
    rt, m = mission()
    drv = DemoApprovalDriver(rt, env=env, receipts_path=str(tmp_path / "r"), clock=clock, home=str(tmp_path))
    assert drv.enabled
    drv.drive(m.id)
    assert m.state == MissionState.SUCCEEDED


def test_non_simulated_runtime_refused(env, clock, tmp_path):
    rt, m = mission()
    rt.demo_simulated = False
    refused("disabled", driver(rt, env, clock, tmp_path).request, m.id)


@pytest.mark.parametrize("name", da._REAL_CREDENTIAL_ENV)
def test_real_credentials_in_env_refused(name, env, clock, tmp_path):
    env[name] = "s3cr3t-value"
    rt, m = mission()
    e = refused("disabled", driver(rt, env, clock, tmp_path).request, m.id)
    assert name in e.reason and "s3cr3t-value" not in e.reason   # names only, never values


@pytest.mark.parametrize("rel", da._REAL_CREDENTIAL_FILES)
def test_real_credential_files_in_home_refused(rel, env, clock, tmp_path):
    f = tmp_path / "home" / rel
    f.parent.mkdir(parents=True)
    f.write_text("x")
    rt, m = mission()
    e = refused("disabled", driver(rt, env, clock, tmp_path).request, m.id)
    assert rel in e.reason


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
    refused("request_tampered", drv.approve, m.id, dataclasses.replace(req, tenant=TENANT + "x"))
    refused("target_mismatch", drv.approve, m.id, signed(drv, req, tenant="acme-production"))
    parked_at(rt, m, "infra.provision")


def test_changing_action_digest_fails(env, clock, tmp_path):
    rt, m = mission()
    drv = driver(rt, env, clock, tmp_path)
    req = drv.request(m.id)
    refused("request_tampered", drv.approve, m.id, dataclasses.replace(req, digest="0" * 64))
    refused("digest_mismatch", drv.approve, m.id, signed(drv, req, digest="0" * 64))
    refused("digest_mismatch", drv.approve, m.id, signed(drv, req, digest=req.digest[:-1] + "x"))
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
    refused("request_tampered", drv.approve, m.id, dataclasses.replace(req, environment="production"))
    refused("target_mismatch", drv.approve, m.id, signed(drv, req, environment="production"))
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
    drv3 = DemoApprovalDriver(rt, _policy(), env=env, receipts_path=str(tmp_path / "other.jsonl"), clock=clock,
                              home=str(tmp_path))
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
    refused("request_tampered", drv.approve, b.id, dataclasses.replace(req_a, mission_id=b.id))
    refused("gate_mismatch", drv.approve, b.id, signed(drv, req_a, mission_id=b.id))
    parked_at(rt, b, "infra.provision")


def test_modified_inputs_fail(env, clock, tmp_path):
    rt, m = mission()
    drv = driver(rt, env, clock, tmp_path)
    req = drv.request(m.id)
    inputs = dict(req.inputs, extra="rm -rf /")
    # even a self-consistent forgery (digest recomputed over the altered inputs) is checked against the live gate
    forged = dataclasses.replace(req, inputs=inputs, digest=da.action_digest(
        req.mission_id, req.node_id, req.capability, req.tenant, req.environment, inputs))
    refused("request_tampered", drv.approve, m.id, forged)
    refused("digest_mismatch", drv.approve, m.id, drv._sign(forged))
    # altered inputs with the original digest
    refused("request_tampered", drv.approve, m.id, dataclasses.replace(req, inputs=inputs))
    refused("digest_mismatch", drv.approve, m.id, signed(drv, req, inputs=inputs))
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
    # demo-shaped names are NOT enough: only the fixed constants (or strict custom_target forms) pass
    for bad in ("demo-acme-bank", "demo-payments-eu", "demo-sandbox2", "demo_sandbox"):
        with pytest.raises(PolicyError):
            _policy(tenant=bad)
    for bad in ("demo-isolated-sim", "isolated-demo2", "demo-env"):
        with pytest.raises(PolicyError):
            _policy(environment=bad)
    with pytest.raises(PolicyError):    # custom values need the explicit flag...
        DemoPolicy("v", (AllowEntry("a.b"),), "demo-sandbox-eu1", ENVNAME, T0 + 9)
    with pytest.raises(PolicyError):    # ...and still the strict form (+ no trailing newline trick)
        DemoPolicy("v", (AllowEntry("a.b"),), "demo-sandbox-eu1\n", ENVNAME, T0 + 9, custom_target=True)
    with pytest.raises(PolicyError):
        AllowEntry("infra.provision\n")
    assert DemoPolicy("v", (AllowEntry("a.b"),), "demo-sandbox-eu1", "isolated-demo-x", T0 + 9, custom_target=True)
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


# ---------------------------------------------------------------- S1: request authentication (timestamps)
def test_tampered_timestamps_fail(env, clock, tmp_path):
    rt, m = mission()
    drv = driver(rt, env, clock, tmp_path, ttl_seconds=60)
    req = drv.request(m.id)
    clock.t += 61
    refused("expired", drv.approve, m.id, req)                       # honestly refused ...
    far = dataclasses.replace(req, expires_at=clock.t + 10_000)       # ... then pushed out by hand
    refused("request_tampered", drv.approve, m.id, far)
    refused("request_tampered", drv.approve, m.id, dataclasses.replace(req, issued_at=clock.t))
    refused("request_tampered", drv.approve, m.id, dataclasses.replace(req, mac=""))
    parked_at(rt, m, "infra.provision")


def test_request_from_another_process_key_is_rejected(env, clock, tmp_path, monkeypatch):
    rt, m = mission()
    drv = driver(rt, env, clock, tmp_path)
    req = drv.request(m.id)
    monkeypatch.setattr(DemoApprovalDriver, "_PROCESS_KEY", b"\x01" * 32)
    refused("request_tampered", drv.approve, m.id, req)


# ---------------------------------------------------------------- S2: policy validation
def _write_policy(tmp_path, **over):
    d = {"version": "v1", "tenant": TENANT, "environment": ENVNAME, "expires_at": "2027-03-01T00:00:00Z",
         "allow": [{"capability": "infra.provision"}]}
    d.update(over)
    f = tmp_path / "pol.json"
    f.write_text(json.dumps(d))     # json.dumps emits NaN / Infinity literally when asked
    return f


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "nan", "not-a-date", None])
def test_policy_expiry_must_be_finite_timestamp(bad, tmp_path):
    with pytest.raises(PolicyError):
        DemoPolicy.load(str(_write_policy(tmp_path, expires_at=bad)))


def test_nan_expiry_object_rejected():
    with pytest.raises(PolicyError):
        DemoPolicy("v", (AllowEntry("a.b"),), TENANT, ENVNAME, float("nan"))


def test_policy_beyond_max_horizon_disabled(env, clock, tmp_path):
    rt, m = mission()
    drv = driver(rt, env, clock, tmp_path, policy=_policy("infra.provision", expires=T0 + 401 * 86400))
    assert "too far" in drv.disabled_reason()
    ok = driver(rt, env, clock, tmp_path, policy=_policy("infra.provision", expires=T0 + 399 * 86400))
    assert ok.enabled


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, True, "5", [1]])
def test_max_cost_validated_at_load(bad):
    with pytest.raises(PolicyError):
        AllowEntry("a.b", max_cost_usd=bad)


# ---------------------------------------------------------------- S3: exactly one approval across drivers/processes
def _grants(rt, m, node):
    return [e for e in rt.repo.timeline(m.id) if e["type"] == "ApprovalGranted" and e["payload"]["node_id"] == node]


def test_two_drivers_racing_approve_once(env, clock, tmp_path):
    rt, m = mission()
    d1, d2 = driver(rt, env, clock, tmp_path), driver(rt, env, clock, tmp_path)
    req = d1.request(m.id)
    node = req.node_id
    barrier, results = threading.Barrier(2), []

    def go(d):
        barrier.wait()
        try:
            results.append(("ok", d.approve(m.id, req)))
        except DemoApprovalRefused as e:
            results.append(("refused", e.code))
    ts = [threading.Thread(target=go, args=(d,)) for d in (d1, d2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(r[0] for r in results) == ["ok", "refused"]
    assert [r[1] for r in results if r[0] == "refused"] == ["replayed"]
    assert len(_grants(rt, m, node)) == 1


def test_two_processes_racing_approve_once(env, clock, tmp_path):
    ctx = multiprocessing.get_context("fork")
    rt, m = mission()
    d = driver(rt, env, clock, tmp_path)
    req = d.request(m.id)
    node, barrier, q = req.node_id, ctx.Barrier(2), ctx.Queue()

    def child():
        barrier.wait()
        try:
            d.approve(m.id, req)
            q.put("ok")
        except DemoApprovalRefused as e:
            q.put(e.code)
        except BaseException as e:  # noqa: BLE001
            q.put("error:" + repr(e))
    ps = [ctx.Process(target=child) for _ in range(2)]
    [p.start() for p in ps]
    got = sorted(q.get(timeout=60) for _ in ps)
    [p.join(30) for p in ps]
    assert got == ["ok", "replayed"], got
    # each forked runtime has its own in-memory log; the shared receipt file shows exactly one granted result
    lines = [json.loads(x) for x in (tmp_path / "r.jsonl").read_text().splitlines()]
    assert sum(1 for x in lines if x["phase"] == "result" and x.get("outcome") == "granted") == 1
    assert sum(1 for x in lines if x["phase"] == "intent") == 1


# ---------------------------------------------------------------- S4: two-phase receipts, failures
def test_kernel_failure_is_refusal_and_does_not_burn_digest(env, clock, tmp_path):
    rt, m = mission()

    def boom(*a, **k):
        raise RuntimeError("kernel exploded")
    drv = driver(rt, env, clock, tmp_path, approve_fn=boom)
    req = drv.request(m.id)
    e = refused("approve_failed", drv.approve, m.id, req)
    assert "kernel exploded" in e.reason
    parked_at(rt, m, "infra.provision")
    types = [x["type"] for x in rt.repo.timeline(m.id) if x["type"].startswith("DemoAutoApproval")]
    assert types == [da.INTENT_EVENT, da.RESULT_EVENT, da.EVENT_TYPE]   # intent, failed result, refusal
    res = [x for x in rt.repo.timeline(m.id) if x["type"] == da.RESULT_EVENT][0]["payload"]
    assert res["outcome"] == "failed" and res["decision"] == "failed"
    # drive() never lets a raw exception escape
    assert drv.drive(m.id)[-1]["decision"] == "refused"
    # the digest is not spent: with a working approve path the same request goes through
    drv._approve = rt.approve
    drv.approve(m.id, req)
    parked_at(rt, m, "infra.configure")
    assert len(_grants(rt, m, req.node_id)) == 1


def test_error_after_kernel_recorded_grant_burns_digest(env, clock, tmp_path):
    rt, m = mission()

    def grant_then_raise(mid, node, decision, edit=None):
        rt.store.append("ApprovalGranted", mid, {"node_id": node, "edit": edit, "capability": "infra.provision"})
        raise RuntimeError("after grant")
    drv = driver(rt, env, clock, tmp_path, approve_fn=grant_then_raise)
    req = drv.request(m.id)
    refused("approve_error_after_grant", drv.approve, m.id, req)
    refused("replayed", drv.approve, m.id, req)


def test_intent_without_result_is_burned(env, clock, tmp_path):
    rt, m = mission()
    drv = driver(rt, env, clock, tmp_path)
    req = drv.request(m.id)
    (tmp_path / "r.jsonl").write_text(json.dumps({"phase": "intent", "digest": req.digest}) + "\n")   # crash mid-approve
    refused("replayed", drv.approve, m.id, req)
    parked_at(rt, m, "infra.provision")


# ---------------------------------------------------------------- S5: policy signature
def test_signed_policy_required_when_key_set(env, clock, tmp_path):
    f = _write_policy(tmp_path)
    env.update({da.ENV_ALLOWLIST: str(f), da.ENV_POLICY_KEY: "k3y"})
    rt, m = mission()

    def mk():
        return DemoApprovalDriver(rt, env=env, receipts_path=str(tmp_path / "r"), clock=clock, home=str(tmp_path))
    assert "signature" in mk().disabled_reason() or "allowlist" in mk().disabled_reason()    # no .sig yet
    sig = tmp_path / "pol.json.sig"
    sig.write_text(hmac.new(b"k3y", f.read_bytes(), hashlib.sha256).hexdigest())
    assert mk().enabled
    f.write_text(f.read_text().replace("infra.provision", "infra.configure"))               # edited after signing
    assert not mk().enabled
    env[da.ENV_POLICY_KEY] = ""                                                              # no key: unsigned allowed
    assert mk().enabled


# ---------------------------------------------------------------- S7: targeted checks
def test_forged_demo_tenant_request_on_prod_tenant_mission_fails(env, clock, tmp_path):
    rt, m = mission(tenant="acme-production")
    drv = driver(rt, env, clock, tmp_path)
    req = drv.request(m.id)
    assert req.tenant == "acme-production"
    forged = signed(drv, req, tenant=TENANT)    # even with a valid MAC, the mission's own tenant is checked
    refused("tenant_mismatch", drv.approve, m.id, forged)
    parked_at(rt, m, "infra.provision")


def test_no_gate_and_unknown_mission(env, clock, tmp_path):
    rt, m = mission()
    drv = driver(rt, env, clock, tmp_path)
    refused("unknown_mission", drv.request, "mission_nope")
    drv.drive(m.id)
    assert m.state == MissionState.SUCCEEDED
    refused("no_gate", drv.request, m.id)


def test_environment_marker_must_stay_designated_between_request_and_approve(env, clock, tmp_path):
    rt, m = mission()
    drv = driver(rt, env, clock, tmp_path)
    req = drv.request(m.id)
    env[da.ENV_ENVIRONMENT] = ENVNAME + "-2"
    e = refused("disabled", drv.approve, m.id, req)
    assert "environment" in e.reason


def test_params_value_wildcards_rejected():
    for v in ("a*", "?", "[ab]", "{x}"):
        with pytest.raises(PolicyError):
            AllowEntry("a.b", params={"k": [v]})


# ---------------------------------------------------------------- nits
def test_corrupt_receipt_lines_are_skipped(env, clock, tmp_path):
    (tmp_path / "r.jsonl").write_text('not json\n[1,2]\n"str"\n\n{"phase": "intent", "digest": "other"}\n')
    rt, m = mission()
    driver(rt, env, clock, tmp_path).drive(m.id)
    assert m.state == MissionState.SUCCEEDED


def test_unreadable_receipt_file_fails_closed(env, clock, tmp_path):
    (tmp_path / "r.jsonl").mkdir()       # a directory where the file should be
    rt, m = mission()
    drv = driver(rt, env, clock, tmp_path)
    req = drv.request(m.id)
    refused("audit_failed", drv.approve, m.id, req)
    parked_at(rt, m, "infra.provision")


def test_sim_runtime_does_not_write_into_a_real_tree(monkeypatch, tmp_path):
    import infra.core as core
    monkeypatch.delenv("INFRA_DEPLOY_ROOT", raising=False)
    monkeypatch.setattr(core, "TF_ROOT", "/nonexistent/original")
    dop.build_runtime(sim=True)
    assert core.TF_ROOT != "/nonexistent/original" and "redevops-demo-sim-" in core.TF_ROOT
    monkeypatch.setenv("INFRA_DEPLOY_ROOT", str(tmp_path))
    monkeypatch.setattr(core, "TF_ROOT", "/explicit")
    dop.build_runtime(sim=True)
    assert core.TF_ROOT == "/explicit"      # an explicit root is respected
