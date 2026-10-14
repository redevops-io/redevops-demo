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
  3. an allowlist policy file is loaded (`REDEVOPS_DEMO_ALLOWLIST`; HMAC-verified when
     `REDEVOPS_DEMO_POLICY_KEY` is set), unexpired, with exact capability names (no wildcards)
  4. the runtime is a simulated one (`rt.demo_simulated is True`) and no cloud credentials are in the environment
  5. the mission's own tenant equals the designated tenant
  6. the request (digest over mission, node, capability, tenant, environment, inputs) matches the live gate,
     is unexpired, addresses this mission, and has never been used before (one-time per digest)

NOT A SECURITY BOUNDARY: this is a safety interlock against accidents (a demo pointed at the wrong target, a
stray flag). A local actor who controls the process environment, the policy file or the Python process can defeat
it. Real protection comes from the demo tenant/credentials being disposable and from the kernel's own gates.

Every decision (approved or refused) is recorded as a receipt: a `DemoAutoApproval` event in the mission's own
event log plus a line in a JSONL receipt file. Approval is written to the log BEFORE the kernel is called.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import os
import pwd
import re
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
import contextlib
import dataclasses
import fcntl
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
DESIGNATED_TENANT = "demo-sandbox"
DESIGNATED_ENVIRONMENT = "isolated-demo"
ENV_POLICY_KEY = "REDEVOPS_DEMO_POLICY_KEY"
MAX_POLICY_HORIZON_S = 400 * 86400.0
INTENT_EVENT, RESULT_EVENT = "DemoAutoApprovalIntent", "DemoAutoApprovalResult"
log = logging.getLogger(__name__)

BANNER = "ISOLATED DEMO: auto-approving allowlisted simulated actions"
DEFAULT_TTL_SECONDS = 300.0

# A non-default target is only accepted when the policy says `custom_target: true` AND it matches these strict forms.
_CUSTOM_TENANT = re.compile(r"demo-sandbox-[a-z0-9]{1,16}")
_CUSTOM_ENVIRONMENT = re.compile(r"isolated-demo-[a-z0-9]{1,16}")
_PROD_WORDS = re.compile(r"(prod|live|real|customer)", re.I)
_WILDCARD = re.compile(r"[*?\[\]{}]")
_CAP_RE = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+")

# Credentials that would let a "demo" action touch a real account. Their presence fails the check closed.
_REAL_CREDENTIAL_ENV = (
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE", "AWS_ROLE_ARN",
    "AWS_WEB_IDENTITY_TOKEN_FILE", "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "AWS_CONTAINER_CREDENTIALS_FULL_URI",
    "AWS_SHARED_CREDENTIALS_FILE", "AZURE_CLIENT_ID", "AZURE_TENANT_ID", "AZURE_CLIENT_SECRET", "ARM_CLIENT_SECRET",
    "GOOGLE_APPLICATION_CREDENTIALS", "DIGITALOCEAN_TOKEN", "DIGITALOCEAN_ACCESS_TOKEN",
    "VAULT_ADDR", "VAULT_TOKEN", "DOCKER_HOST", "SSH_AUTH_SOCK", "KUBECONFIG", "KUBERNETES_SERVICE_HOST",
)
# credential files in the effective user's HOME (relative paths)
_REAL_CREDENTIAL_FILES = (".aws/credentials", ".kube/config", ".vault-token")


class DemoApprovalRefused(Exception):
    """Fail-closed refusal: nothing was approved, the mission stays parked for a human."""

    def __init__(self, reason: str, code: str):
        super().__init__(f"{code}: {reason}")
        self.reason, self.code = reason, code


class PolicyError(ValueError):
    """The allowlist policy is malformed or unsafe (e.g. a wildcard)."""


def _reject_constant(name):
    raise PolicyError(f"non-finite number {name} in policy")


def _utcnow() -> float:
    return time.time()


def _parse_ts(value: Any) -> float:
    if isinstance(value, bool):
        raise PolicyError("expires_at must be a timestamp")
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise PolicyError(f"expires_at must be finite, got {value!r}")
        return float(value)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as e:
        raise PolicyError(f"expires_at is not an ISO-8601 timestamp: {value!r}") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _effective_home() -> str:
    try:
        return pwd.getpwuid(os.geteuid()).pw_dir
    except KeyError:
        return os.path.expanduser("~")


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
        if not isinstance(cap, str) or _WILDCARD.search(cap) or not _CAP_RE.fullmatch(cap):
            raise PolicyError(f"allowlist capability must be an exact name like 'infra.provision', got {cap!r}")
        mc = self.max_cost_usd
        if mc is not None and (isinstance(mc, bool) or not isinstance(mc, (int, float))
                               or not math.isfinite(mc) or mc < 0):
            raise PolicyError(f"max_cost_usd must be a finite number >= 0, got {mc!r}")
        if self.params is not None and not isinstance(self.params, dict):
            raise PolicyError("allowlist params must be an object")
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
    custom_target: bool = False

    def __post_init__(self):
        if isinstance(self.expires_at, bool) or not isinstance(self.expires_at, (int, float)) \
                or not math.isfinite(self.expires_at):
            raise PolicyError(f"expires_at must be a finite timestamp, got {self.expires_at!r}")
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
        for label, val, fixed, custom in (
                ("tenant", self.tenant, DESIGNATED_TENANT, _CUSTOM_TENANT),
                ("environment", self.environment, DESIGNATED_ENVIRONMENT, _CUSTOM_ENVIRONMENT)):
            if not isinstance(val, str) or _PROD_WORDS.search(val):
                raise PolicyError(f"designated {label} is not a disposable demo value: {val!r}")
            if val != fixed and not (self.custom_target is True and custom.fullmatch(val)):
                raise PolicyError(f"designated {label} must be {fixed!r} (or, with custom_target: true, match "
                                  f"{custom.pattern!r}), got {val!r}")

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
                   environment=d.get("environment", ""), expires_at=_parse_ts(d.get("expires_at")),
                   custom_target=d.get("custom_target", False))

    @classmethod
    def load(cls, path: str, key: str | bytes | None = None) -> "DemoPolicy":
        """Load the policy file. With `key`, `<path>.sig` must hold the hex HMAC-SHA256 of the file bytes."""
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
            if key:
                kb = key.encode() if isinstance(key, str) else key
                with open(path + ".sig") as fh:
                    sig = fh.read().strip()
                if not hmac.compare_digest(sig, hmac.new(kb, raw, hashlib.sha256).hexdigest()):
                    raise PolicyError("policy signature does not match")
            return cls.from_dict(json.loads(raw, parse_constant=_reject_constant))
        except (OSError, ValueError) as e:
            if isinstance(e, PolicyError):
                raise
            raise PolicyError(f"cannot read allowlist {path}: {e}") from e

    def entry_for(self, capability: str) -> AllowEntry | None:
        return next((e for e in self.allow if e.capability == capability), None)


@dataclass(frozen=True)
class ApprovalRequest:
    """An approval request. `digest` is the action digest (replay key). `mac` authenticates EVERY other field,
    including the timestamps, with a per-process key: an edited request (e.g. a pushed-out `expires_at`) is refused."""
    mission_id: str
    node_id: str
    capability: str
    tenant: str
    environment: str
    inputs: dict
    digest: str
    issued_at: float
    expires_at: float
    mac: str = ""


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
    _PROCESS_KEY = secrets.token_bytes(32)      # authenticates requests issued by this process
    _PROC_LOCK = threading.RLock()              # serialises claim->approve across driver instances in a process

    def __init__(self, rt, policy: DemoPolicy | None = None, *, env: dict | None = None,
                 receipts_path: str | None = None, clock: Callable[[], float] = _utcnow,
                 ttl_seconds: float = DEFAULT_TTL_SECONDS, approve_fn: Callable | None = None,
                 home: str | None = None):
        self.rt = rt
        self._env = os.environ if env is None else env
        self._clock = clock
        self._ttl = ttl_seconds
        self._home = home if home is not None else _effective_home()
        self._policy_error = ""
        self.policy = policy
        if self.policy is None:
            path = self._env.get(ENV_ALLOWLIST, "")
            if path:
                try:
                    self.policy = DemoPolicy.load(path, self._env.get(ENV_POLICY_KEY) or None)
                except PolicyError as e:
                    self._policy_error = str(e)
            else:
                self._policy_error = f"{ENV_ALLOWLIST} not set"
        self.receipts_path = receipts_path or self._env.get(ENV_RECEIPTS) or os.path.join(
            ".redevops-demo", "receipts.jsonl")
        # `approve_fn` is the authenticated approve path; default is the in-process kernel call a human UI makes.
        self._approve = approve_fn or rt.approve

    # -- enablement (fail closed) -----------------------------------------------------------------
    def disabled_reason(self) -> str | None:
        env = self._env
        if env.get(ENV_MODE) != MODE_ISOLATED:
            return f"auto-approval is disabled: {ENV_MODE}!={MODE_ISOLATED}"
        if self.policy is None:
            return f"no valid allowlist policy ({self._policy_error})"
        now = self._clock()
        if now >= self.policy.expires_at:
            return f"policy {self.policy.version} expired"
        if self.policy.expires_at - now > MAX_POLICY_HORIZON_S:
            return f"policy {self.policy.version} expires too far in the future (max 400 days)"
        if env.get(ENV_TENANT) != self.policy.tenant:
            return "target tenant marker does not equal the designated demo tenant"
        if env.get(ENV_ENVIRONMENT) != self.policy.environment:
            return "target environment marker does not equal the designated demo environment"
        if getattr(self.rt, "demo_simulated", False) is not True:
            return "runtime is not a simulated demo runtime"
        present = [k for k in _REAL_CREDENTIAL_ENV if env.get(k)]
        present += [f"~/{f}" for f in _REAL_CREDENTIAL_FILES if os.path.exists(os.path.join(self._home, f))]
        if present:
            return f"real credentials present in environment ({', '.join(present)}); refusing"
        return None

    @property
    def enabled(self) -> bool:
        return self.disabled_reason() is None

    # -- request authentication ---------------------------------------------------------------------
    def _mac(self, r: ApprovalRequest) -> str:
        body = canonical_json({"mission_id": r.mission_id, "node_id": r.node_id, "capability": r.capability,
                               "tenant": r.tenant, "environment": r.environment, "inputs": r.inputs,
                               "digest": r.digest, "issued_at": r.issued_at, "expires_at": r.expires_at})
        return hmac.new(self._PROCESS_KEY, body.encode(), hashlib.sha256).hexdigest()

    def _sign(self, r: ApprovalRequest) -> ApprovalRequest:
        return dataclasses.replace(r, mac=self._mac(r))

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
        """Build the (authenticated) approval request for the mission's open gate. Does not approve anything."""
        try:
            why = self.disabled_reason()
            if why:
                raise DemoApprovalRefused(why, "disabled")
            pending, inputs, m = self._live_gate(mission_id)
            tenant = getattr(m, "tenant", "") or ""
            environment = self._env.get(ENV_ENVIRONMENT, "")
            now = self._clock()
            return self._sign(ApprovalRequest(
                mission_id=mission_id, node_id=pending["node_id"], capability=pending.get("capability", ""),
                tenant=tenant, environment=environment, inputs=inputs,
                digest=action_digest(mission_id, pending["node_id"], pending.get("capability", ""),
                                     tenant, environment, inputs),
                issued_at=now, expires_at=min(now + self._ttl, self.policy.expires_at)))
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
        if not isinstance(req, ApprovalRequest) or not hmac.compare_digest(str(req.mac), self._mac(req)):
            raise DemoApprovalRefused("request was not issued by this driver or was modified", "request_tampered")
        if req.mission_id != mission_id:
            raise DemoApprovalRefused("request was issued for a different mission", "cross_mission")
        now = self._clock()
        if now >= req.expires_at or now >= pol.expires_at:
            raise DemoApprovalRefused("approval request expired", "expired")
        if req.tenant != pol.tenant or req.environment != pol.environment:
            raise DemoApprovalRefused("request tenant/environment is not the designated demo target", "target_mismatch")
        # Serialise "check log -> write intent -> approve -> write result" across drivers and processes.
        with self._claim_lock():
            if self._burned(mission_id, req.digest):
                raise DemoApprovalRefused("action digest already used (replay)", "replayed")
            pending, live_inputs, m = self._live_gate(mission_id)
            if (getattr(m, "tenant", "") or "") != pol.tenant:
                raise DemoApprovalRefused("mission tenant is not the designated demo tenant", "tenant_mismatch")
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
            edit = approval_edit(self.rt, mission_id, req.capability)
            base = dict(mission_id=mission_id, node_id=req.node_id, capability=req.capability,
                        tenant=req.tenant, environment=req.environment, digest=req.digest)
            before = self._grants(mission_id, req.node_id)
            intent = self._write(INTENT_EVENT, "intent", "approve", base)     # audit first; fail closed if unwritable
            try:
                # the same authenticated entry point a human approver uses; the kernel enforces every gate on it
                if edit is not None:
                    self._approve(mission_id, req.node_id, "approve", edit=edit)
                else:
                    self._approve(mission_id, req.node_id, "approve")
            except Exception as e:  # noqa: BLE001 -- kernel refused or failed; the mission stays parked/as-is
                granted = self._grants(mission_id, req.node_id) > before   # did the kernel record the approval?
                self._write(RESULT_EVENT, "result", "approve" if granted else "failed", base,
                            outcome="granted" if granted else "failed", intent=intent["receipt_id"],
                            reason=f"{type(e).__name__}: {e}")
                if granted:   # the approval is on the log: the digest is spent, do not pretend otherwise
                    raise DemoApprovalRefused(f"kernel recorded the approval but raised afterwards: {e}",
                                              "approve_error_after_grant") from e
                raise DemoApprovalRefused(f"kernel approve failed: {e}", "approve_failed") from e
            return self._write(RESULT_EVENT, "result", "approve", base, outcome="granted",
                               intent=intent["receipt_id"])

    # -- claim / replay state -----------------------------------------------------------------------
    @contextlib.contextmanager
    def _claim_lock(self):
        with self._PROC_LOCK:
            try:
                os.makedirs(os.path.dirname(self.receipts_path) or ".", exist_ok=True)
                fd = os.open(self.receipts_path + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
            except OSError as e:
                raise DemoApprovalRefused(f"cannot take the claim lock: {e}", "audit_failed") from None
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                os.close(fd)     # closing releases the flock

    def _grants(self, mission_id: str, node_id: str) -> int:
        return sum(1 for e in self.rt.repo.timeline(mission_id)
                   if e["type"] == "ApprovalGranted" and e["payload"].get("node_id") == node_id)

    def _burned(self, mission_id: str, digest: str) -> bool:
        """A digest is spent once an approval was granted, or an intent was written and never resolved (a crash
        mid-approve is ambiguous, so it fails closed). A recorded failure with no ApprovalGranted does not burn it."""
        state = None
        for rec in self._read_receipts():
            if rec.get("digest") != digest or rec.get("phase") not in ("intent", "result"):
                continue
            if rec["phase"] == "intent":
                state = "pending"
            elif rec.get("outcome") == "granted":
                state = "granted"
            elif state != "granted":
                state = "failed"
        if state in ("pending", "granted"):
            return True
        for e in self.rt.repo.timeline(mission_id):    # same runtime, other receipt file
            if e["payload"].get("digest") == digest:
                if e["type"] == RESULT_EVENT and e["payload"].get("outcome") == "granted":
                    return True
        return False

    def _read_receipts(self) -> list[dict]:
        out = []
        try:
            fh = open(self.receipts_path)
        except FileNotFoundError:
            return out
        except OSError as e:
            raise DemoApprovalRefused(f"receipt file unreadable: {e}", "audit_failed") from None
        with fh:
            for n, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    rec = None
                if isinstance(rec, dict):
                    out.append(rec)
                else:
                    log.warning("skipping corrupt receipt line %d in %s", n, self.receipts_path)
        return out

    # -- audit ------------------------------------------------------------------------------------
    def _write(self, event_type: str, phase: str, decision: str, base: dict, **extra) -> dict:
        rec = {
            "receipt_id": "dar_" + uuid.uuid4().hex[:12], "actor": ACTOR,
            "policy_version": self.policy.version if self.policy else "",
            "phase": phase, "decision": decision, "ts": self._clock(), **base, **extra,
        }
        try:
            os.makedirs(os.path.dirname(self.receipts_path) or ".", exist_ok=True)
            with open(self.receipts_path, "a") as fh:
                fh.write(json.dumps(rec, sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            if rec["mission_id"] in getattr(self.rt, "_missions", {}):
                self.rt.store.append(event_type, rec["mission_id"], dict(rec))
        except Exception as e:  # noqa: BLE001
            raise DemoApprovalRefused(f"could not write the audit receipt: {e}", "audit_failed") from None
        return rec

    def _receipt(self, mission_id, decision, refusal, *, node_id, capability, tenant, environment, digest) -> dict:
        """Best-effort refusal record (never raises: a refusal must still be reported)."""
        rec = {"receipt_id": "dar_" + uuid.uuid4().hex[:12], "actor": ACTOR,
               "policy_version": self.policy.version if self.policy else "", "phase": "refusal",
               "decision": decision, "digest": digest, "mission_id": mission_id, "node_id": node_id,
               "capability": capability, "tenant": tenant, "environment": environment, "ts": self._clock(),
               "reason": refusal.reason, "code": refusal.code}
        try:
            if mission_id in getattr(self.rt, "_missions", {}):
                self.rt.store.append(EVENT_TYPE, mission_id, dict(rec))
        except Exception:  # noqa: BLE001
            pass
        try:
            os.makedirs(os.path.dirname(self.receipts_path) or ".", exist_ok=True)
            with open(self.receipts_path, "a") as fh:
                fh.write(json.dumps(rec, sort_keys=True) + "\n")
        except OSError:
            pass
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
