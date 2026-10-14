"""Isolated-demo auto-approval driver.

Auto-approval is a *demo convenience*, never a bypass. `DemoApprovalDriver` approves a parked mission gate
through the SAME authenticated path a human approver uses (`MissionRuntime.approve`); the kernel's own checks
(mandatory-capability gate, `plan_digest_required`, the signed approval token the runtime mints for the
operator and the operator verifies) all still run. The driver only decides *whether to press the button*, and
it fails CLOSED: any doubt leaves the mission parked for a human and logs why.

It may press the button only when ALL of these hold:
  1. `REDEVOPS_DEMO_MODE=isolated`
  2. the ambient markers `REDEVOPS_DEMO_TENANT` / `REDEVOPS_DEMO_ENVIRONMENT` equal the policy's designated
     disposable demo tenant / environment (and those look like demo values, not production ones)
  3. a signed-off allowlist policy file is loaded (`REDEVOPS_DEMO_ALLOWLIST`), unexpired, with exact
     capability names (no wildcards)
  4. the runtime is a simulated one (`rt.demo_simulated is True`) and no cloud credentials are in the environment
  5. the mission's own tenant equals the designated tenant
  6. the request (digest over mission, node, capability, tenant, environment, inputs) matches the live gate,
     is unexpired, addresses this mission, and has never been used before (one-time per digest)

Every decision (approved or refused) is recorded as a receipt: a `DemoAutoApproval` event in the mission's own
event log plus a line in a JSONL receipt file. Approval is written to the log BEFORE the kernel is called.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

ACTOR = "demo_driver"
EVENT_TYPE = "DemoAutoApproval"

ENV_MODE = "REDEVOPS_DEMO_MODE"
MODE_ISOLATED = "isolated"
ENV_TENANT = "REDEVOPS_DEMO_TENANT"
ENV_ENVIRONMENT = "REDEVOPS_DEMO_ENVIRONMENT"
ENV_ALLOWLIST = "REDEVOPS_DEMO_ALLOWLIST"
ENV_RECEIPTS = "REDEVOPS_DEMO_RECEIPTS"

# The designated disposable demo values. The policy file must carry these (or other demo-shaped values) and the
# ambient markers must equal the policy's; nothing here is a default that enables anything by itself.
DESIGNATED_TENANT = "demo-sandbox-tenant"
DESIGNATED_ENVIRONMENT = "demo-isolated-sim"

BANNER = "ISOLATED DEMO: auto-approving allowlisted simulated actions"
DEFAULT_TTL_SECONDS = 300.0

_DEMO_SHAPED = re.compile(r"^demo[-_][a-z0-9][a-z0-9_-]*$")
_PROD_WORDS = re.compile(r"(prod|live|real|customer)", re.I)
_WILDCARD = re.compile(r"[*?\[\]{}]")
_CAP_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")

# Credentials that would let a "demo" action touch a real account. Their presence fails the check closed.
_REAL_CREDENTIAL_ENV = (
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE",
    "AZURE_CLIENT_SECRET", "ARM_CLIENT_SECRET", "GOOGLE_APPLICATION_CREDENTIALS",
    "DIGITALOCEAN_TOKEN", "DIGITALOCEAN_ACCESS_TOKEN", "VAULT_TOKEN", "KUBECONFIG",
)


class DemoApprovalRefused(Exception):
    """Fail-closed refusal: nothing was approved, the mission stays parked for a human."""

    def __init__(self, reason: str, code: str):
        super().__init__(f"{code}: {reason}")
        self.reason, self.code = reason, code


class PolicyError(ValueError):
    """The allowlist policy is malformed or unsafe (e.g. a wildcard)."""


def _utcnow() -> float:
    return time.time()


def _parse_ts(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as e:
        raise PolicyError(f"expires_at is not an ISO-8601 timestamp: {value!r}") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def action_digest(mission_id: str, node_id: str, capability: str, tenant: str, environment: str,
                  inputs: dict) -> str:
    """sha256 over canonical JSON of (mission id, node id, capability, tenant, environment, inputs)."""
    body = {"mission_id": mission_id, "node_id": node_id, "capability": capability,
            "tenant": tenant, "environment": environment, "inputs": inputs}
    return hashlib.sha256(canonical_json(body).encode()).hexdigest()


@dataclass(frozen=True)
class AllowEntry:
    """One auto-approvable action type. `capability` is an EXACT name. `params`, when given, is the permitted
    input shape: every input key must be listed; a list value restricts it to those values, null permits any.
    `max_cost_usd` caps the cost estimate carried in the inputs (key `est_cost_usd`), if any."""
    capability: str
    params: dict | None = None
    max_cost_usd: float | None = None

    def __post_init__(self):
        cap = self.capability
        if not isinstance(cap, str) or _WILDCARD.search(cap) or not _CAP_RE.match(cap):
            raise PolicyError(f"allowlist capability must be an exact name like 'infra.provision', got {cap!r}")
        for k, v in (self.params or {}).items():
            if v is not None and (not isinstance(v, list) or not v or any(
                    isinstance(x, str) and _WILDCARD.search(x) for x in v)):
                raise PolicyError(f"allowlist params[{k!r}] must be null or a non-empty list of exact values")

    def permits(self, inputs: dict) -> str | None:
        """None if the inputs fit the entry, else the reason they do not."""
        if self.params is not None:
            for k, v in inputs.items():
                if k not in self.params:
                    return f"input {k!r} not in the allowed shape for {self.capability}"
                allowed = self.params[k]
                if allowed is not None and v not in allowed:
                    return f"input {k!r} value not allowed for {self.capability}"
        elif inputs and any(not (isinstance(v, dict) and set(v) == {"$from_world"}) for v in inputs.values()):
            # no params declared: only plain references to upstream outcomes are accepted, no literals
            return f"literal inputs not allowed for {self.capability} (declare params)"
        if self.max_cost_usd is not None:
            cost = inputs.get("est_cost_usd")
            if not isinstance(cost, (int, float)) or cost > self.max_cost_usd:
                return f"cost exceeds max_cost_usd for {self.capability}"
        return None


@dataclass(frozen=True)
class DemoPolicy:
    version: str
    allow: tuple
    tenant: str
    environment: str
    expires_at: float

    def __post_init__(self):
        if not self.version or not isinstance(self.version, str):
            raise PolicyError("policy needs a version string")
        if not self.allow:
            raise PolicyError("policy allowlist is empty")
        seen = set()
        for e in self.allow:
            if not isinstance(e, AllowEntry):
                raise PolicyError("allow entries must be AllowEntry")
            if e.capability in seen:
                raise PolicyError(f"duplicate allowlist entry {e.capability}")
            seen.add(e.capability)
        for label, val in (("tenant", self.tenant), ("environment", self.environment)):
            if not isinstance(val, str) or not _DEMO_SHAPED.match(val) or _PROD_WORDS.search(val):
                raise PolicyError(f"designated {label} must look like a disposable demo value (demo-...), got {val!r}")

    @classmethod
    def from_dict(cls, d: dict) -> "DemoPolicy":
        if not isinstance(d, dict):
            raise PolicyError("policy must be a JSON object")
        allow = d.get("allow")
        if not isinstance(allow, list):
            raise PolicyError("policy 'allow' must be a list of entries")
        entries = []
        for raw in allow:
            if not isinstance(raw, dict) or "capability" not in raw:
                raise PolicyError("allow entries must be objects with a 'capability'")
            unknown = set(raw) - {"capability", "params", "max_cost_usd"}
            if unknown:
                raise PolicyError(f"unknown allow-entry keys {sorted(unknown)}")
            entries.append(AllowEntry(raw["capability"], raw.get("params"), raw.get("max_cost_usd")))
        return cls(version=d.get("version", ""), allow=tuple(entries), tenant=d.get("tenant", ""),
                   environment=d.get("environment", ""), expires_at=_parse_ts(d.get("expires_at")))

    @classmethod
    def load(cls, path: str) -> "DemoPolicy":
        try:
            with open(path) as fh:
                return cls.from_dict(json.load(fh))
        except (OSError, json.JSONDecodeError) as e:
            raise PolicyError(f"cannot read allowlist {path}: {e}") from e

    def entry_for(self, capability: str) -> AllowEntry | None:
        return next((e for e in self.allow if e.capability == capability), None)


@dataclass(frozen=True)
class ApprovalRequest:
    mission_id: str
    node_id: str
    capability: str
    tenant: str
    environment: str
    inputs: dict
    digest: str
    issued_at: float
    expires_at: float


def plan_digest_of(rt, mission_id: str) -> str:
    """The `plan_digest` the infra.plan step emitted (sha256 of the saved plan file) -- the identity a human approves."""
    digest = ""
    for e in rt.repo.timeline(mission_id):
        if e["type"] == "NodeSucceeded" and e["payload"].get("capability") == "infra.plan":
            digest = (e["payload"].get("result") or {}).get("plan_digest") or ""
    return digest


def approval_edit(rt, mission_id: str, capability: str) -> dict | None:
    """The typed edit a human approver passes for this gate. Kernel >= v0.3.26 refuses `terraform apply` unless the
    approval carries the digest of the plan that was reviewed (`plan_digest_required`)."""
    if capability == "infra.provision":
        return {"plan_digest": plan_digest_of(rt, mission_id)}
    return None


class DemoApprovalDriver:
    def __init__(self, rt, policy: DemoPolicy | None = None, *, env: dict | None = None,
                 receipts_path: str | None = None, clock: Callable[[], float] = _utcnow,
                 ttl_seconds: float = DEFAULT_TTL_SECONDS, approve_fn: Callable | None = None):
        self.rt = rt
        self._env = os.environ if env is None else env
        self._clock = clock
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._used: set[str] = set()
        self._policy_error = ""
        self.policy = policy
        if self.policy is None:
            path = self._env.get(ENV_ALLOWLIST, "")
            if path:
                try:
                    self.policy = DemoPolicy.load(path)
                except PolicyError as e:
                    self._policy_error = str(e)
            else:
                self._policy_error = f"{ENV_ALLOWLIST} not set"
        self.receipts_path = receipts_path or self._env.get(ENV_RECEIPTS) or os.path.join(
            ".redevops-demo", "receipts.jsonl")
        # one-time use must survive a restart: re-learn consumed digests from the receipt file
        try:
            with open(self.receipts_path) as fh:
                for line in fh:
                    rec = json.loads(line)
                    if rec.get("decision") == "approve" and rec.get("digest"):
                        self._used.add(rec["digest"])
        except (OSError, ValueError):
            pass
        # `approve_fn` is the authenticated approve path; default is the in-process kernel call a human UI makes.
        self._approve = approve_fn or rt.approve

    # -- enablement (fail closed) -----------------------------------------------------------------
    def disabled_reason(self) -> str | None:
        env = self._env
        if env.get(ENV_MODE) != MODE_ISOLATED:
            return f"auto-approval is disabled: {ENV_MODE}!={MODE_ISOLATED}"
        if self.policy is None:
            return f"no valid allowlist policy ({self._policy_error})"
        if self._clock() >= self.policy.expires_at:
            return f"policy {self.policy.version} expired"
        if env.get(ENV_TENANT) != self.policy.tenant:
            return "target tenant marker does not equal the designated demo tenant"
        if env.get(ENV_ENVIRONMENT) != self.policy.environment:
            return "target environment marker does not equal the designated demo environment"
        if getattr(self.rt, "demo_simulated", False) is not True:
            return "runtime is not a simulated demo runtime"
        present = [k for k in _REAL_CREDENTIAL_ENV if env.get(k)]
        if present:
            return f"real credentials present in environment ({', '.join(present)}); refusing"
        return None

    @property
    def enabled(self) -> bool:
        return self.disabled_reason() is None

    # -- request / approve ------------------------------------------------------------------------
    def _live_gate(self, mission_id: str) -> tuple[dict, dict, Any]:
        """(pending human task, the live node inputs, mission). Raises if there is nothing approvable."""
        m = self.rt._missions.get(mission_id)
        if m is None:
            raise DemoApprovalRefused("unknown mission", "unknown_mission")
        pending = self.rt.repo.pending_human(mission_id)
        if not pending:
            raise DemoApprovalRefused("mission is not parked at an approval gate", "no_gate")
        if pending.get("kind") in ("verification", "disambiguation"):
            raise DemoApprovalRefused(f"a {pending['kind']} gate needs a human", "not_an_approval_gate")
        plan = self.rt._plans.get(mission_id)
        node = next((n for n in (plan.graph.nodes if plan else []) if n.id == pending["node_id"]), None)
        if node is None:
            raise DemoApprovalRefused("gate node not found in the plan", "no_node")
        inputs = dict(node.inputs or {})
        pd = plan_digest_of(self.rt, mission_id)
        if pd:   # bind the exact plan the gate is about, not just the node's references
            inputs["_plan_digest"] = pd
        return pending, inputs, m

    def request(self, mission_id: str) -> ApprovalRequest:
        """Build the approval request for the mission's open gate. Does not approve anything."""
        try:
            why = self.disabled_reason()
            if why:
                raise DemoApprovalRefused(why, "disabled")
            pending, inputs, m = self._live_gate(mission_id)
            tenant = getattr(m, "tenant", "") or ""
            environment = self._env.get(ENV_ENVIRONMENT, "")
            now = self._clock()
            return ApprovalRequest(
                mission_id=mission_id, node_id=pending["node_id"], capability=pending.get("capability", ""),
                tenant=tenant, environment=environment, inputs=inputs,
                digest=action_digest(mission_id, pending["node_id"], pending.get("capability", ""),
                                     tenant, environment, inputs),
                issued_at=now, expires_at=min(now + self._ttl, self.policy.expires_at))
        except DemoApprovalRefused as e:
            self._receipt(mission_id, "refused", e, node_id="", capability="", tenant="", environment="", digest="")
            raise

    def approve(self, mission_id: str, request: ApprovalRequest):
        """Approve `request` on `mission_id`, or raise DemoApprovalRefused leaving the mission parked."""
        try:
            return self._approve_checked(mission_id, request)
        except DemoApprovalRefused as e:
            self._receipt(mission_id, "refused", e, node_id=getattr(request, "node_id", ""),
                          capability=getattr(request, "capability", ""), tenant=getattr(request, "tenant", ""),
                          environment=getattr(request, "environment", ""), digest=getattr(request, "digest", ""))
            raise

    def _approve_checked(self, mission_id: str, req: ApprovalRequest):
        why = self.disabled_reason()
        if why:
            raise DemoApprovalRefused(why, "disabled")
        pol = self.policy
        if req.mission_id != mission_id:
            raise DemoApprovalRefused("request was issued for a different mission", "cross_mission")
        if req.digest in self._used or req.digest in self._logged_digests(mission_id):
            raise DemoApprovalRefused("action digest already used (replay)", "replayed")
        now = self._clock()
        if now >= req.expires_at or now >= pol.expires_at:
            raise DemoApprovalRefused("approval request expired", "expired")
        if req.tenant != pol.tenant or req.environment != pol.environment:
            raise DemoApprovalRefused("request tenant/environment is not the designated demo target", "target_mismatch")
        pending, live_inputs, m = self._live_gate(mission_id)
        if (getattr(m, "tenant", "") or "") != pol.tenant:
            raise DemoApprovalRefused("mission tenant is not the designated demo tenant", "tenant_mismatch")
        if req.environment != self._env.get(ENV_ENVIRONMENT):
            raise DemoApprovalRefused("environment changed since the request was issued", "environment_mismatch")
        if req.node_id != pending["node_id"] or req.capability != pending.get("capability"):
            raise DemoApprovalRefused("request does not match the open gate", "gate_mismatch")
        # recompute from the LIVE gate: a request whose fields, inputs or digest were altered cannot match
        live = action_digest(mission_id, pending["node_id"], pending.get("capability", ""),
                             pol.tenant, pol.environment, live_inputs)
        own = action_digest(req.mission_id, req.node_id, req.capability, req.tenant, req.environment, req.inputs)
        if not (req.digest == own == live):
            raise DemoApprovalRefused("action digest mismatch (request modified or stale)", "digest_mismatch")
        entry = pol.entry_for(req.capability)
        if entry is None:
            raise DemoApprovalRefused(f"{req.capability} is not on the auto-approve allowlist", "not_allowlisted")
        shape = entry.permits({k: v for k, v in live_inputs.items() if k != "_plan_digest"})
        if shape:
            raise DemoApprovalRefused(shape, "params_not_allowed")
        with self._lock:
            if req.digest in self._used:
                raise DemoApprovalRefused("action digest already used (replay)", "replayed")
            self._used.add(req.digest)       # consume first: a crash after this point can never double-approve
        receipt = self._receipt(mission_id, "approve", None, node_id=req.node_id, capability=req.capability,
                                tenant=req.tenant, environment=req.environment, digest=req.digest)
        edit = approval_edit(self.rt, mission_id, req.capability)
        # the same authenticated entry point a human approver uses; the kernel still enforces every gate on it
        if edit is not None:
            self._approve(mission_id, req.node_id, "approve", edit=edit)
        else:
            self._approve(mission_id, req.node_id, "approve")
        return receipt

    def _logged_digests(self, mission_id: str) -> set:
        try:
            return {e["payload"].get("digest") for e in self.rt.repo.timeline(mission_id)
                    if e["type"] == EVENT_TYPE and e["payload"].get("decision") == "approve"}
        except Exception:  # noqa: BLE001
            return set()

    # -- audit ------------------------------------------------------------------------------------
    def _receipt(self, mission_id, decision, refusal, *, node_id, capability, tenant, environment, digest) -> dict:
        rec = {
            "receipt_id": "dar_" + uuid.uuid4().hex[:12],
            "actor": ACTOR,
            "policy_version": self.policy.version if self.policy else "",
            "decision": decision,
            "digest": digest,
            "mission_id": mission_id,
            "node_id": node_id,
            "capability": capability,
            "tenant": tenant,
            "environment": environment,
            "ts": self._clock(),
        }
        if refusal is not None:
            rec["reason"] = refusal.reason
            rec["code"] = refusal.code
        # in-mission note (the kernel's event log is the audit trail the cockpit shows) ...
        try:
            if mission_id in getattr(self.rt, "_missions", {}):
                self.rt.store.append(EVENT_TYPE, mission_id, dict(rec))
        except Exception:  # noqa: BLE001 -- a refusal must still be reported
            if decision == "approve":
                raise DemoApprovalRefused("could not write the mission audit event", "audit_failed") from None
        # ... plus a JSONL receipt file
        try:
            os.makedirs(os.path.dirname(self.receipts_path) or ".", exist_ok=True)
            with open(self.receipts_path, "a") as fh:
                fh.write(json.dumps(rec, sort_keys=True) + "\n")
        except OSError:
            if decision == "approve":
                raise DemoApprovalRefused("could not write the receipt file", "audit_failed") from None
        return rec

    # -- convenience ------------------------------------------------------------------------------
    def drive(self, mission_id: str, *, max_gates: int = 8) -> list[dict]:
        """Approve every consecutive gate the policy allows; stop (parked) at the first refusal.
        Returns the receipts, the last of which may be a refusal."""
        out: list[dict] = []
        for _ in range(max_gates):
            if not self.rt.repo.pending_human(mission_id):
                break
            try:
                req = self.request(mission_id)
                out.append(self.approve(mission_id, req))
            except DemoApprovalRefused as e:
                out.append({"decision": "refused", "code": e.code, "reason": e.reason, "actor": ACTOR})
                break
        return out


# -- CLI glue shared by the mission drivers (`--auto-approve`) ------------------------------------
def add_auto_approve_arg(parser) -> None:
    parser.add_argument("--auto-approve", action="store_true",
                        help="auto-approve allowlisted SIMULATED gates; only works with REDEVOPS_DEMO_MODE=isolated "
                             "(see README: Isolated demo auto-approval). Otherwise the mission parks for a human.")


def demo_tenant(auto_approve: bool, env: dict | None = None) -> str:
    """Tenant for a mission about to be auto-approved: the ambient designated one; empty otherwise."""
    return (env if env is not None else os.environ).get(ENV_TENANT, "") if auto_approve else ""


def run_auto_approve(rt, mission_id: str, *, out=print, driver: DemoApprovalDriver | None = None) -> list[dict]:
    """Drive the open gate(s) of `mission_id` through DemoApprovalDriver. Prints the banner only when the isolated
    demo mode is actually enabled; otherwise states why it is NOT auto-approving and leaves the mission parked."""
    drv = driver or DemoApprovalDriver(rt)
    why = drv.disabled_reason()
    if why:
        out(f"auto-approval NOT enabled ({why}); mission stays parked for a human approver")
        return []
    out(BANNER)
    receipts = drv.drive(mission_id)
    for r in receipts:
        if r.get("decision") == "approve":
            out(f"  approved {r['capability']} actor={r['actor']} policy={r['policy_version']} "
                f"digest={r['digest'][:12]} receipt={r['receipt_id']}")
        else:
            out(f"  REFUSED ({r.get('code')}): {r.get('reason')} -- mission stays parked for a human approver")
    return receipts
