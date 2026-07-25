"""Vault → service-principal credentials for the ReDevOps Azure demo.

Azure has no `sts:AssumeRole`; the equivalent security boundary is a scoped **service principal**
(SP) whose RBAC role assignments are the least-privilege grant. A single SP (tenant_id, client_id,
client_secret) is read from Vault and handed out as an ``azure-identity`` ``ClientSecretCredential``
paired with the subscription id — the analog of a role-scoped ``boto3.Session``.

We keep the AWS demo's ``deployer / agent / readonly`` role notion so preflight/doctor stay uniform
across clouds, but on Azure they resolve to the same SP by default (differentiate later with
per-role SPs or RBAC scopes in ``secret/redevops/azure-demo/config`` — the seam is here). The proof
that "the credential works" is a cheap authenticated read (list resource groups), not an assume-role.

HARD RULE: key material (client secret, tokens) is NEVER logged, printed, or placed in a repr/str.
Tests assert this — see the redacting ``__repr__`` on both classes below.
"""
from __future__ import annotations

import json
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional

Role = Literal["deployer", "agent", "readonly"]
_ROLES: List[Role] = ["deployer", "agent", "readonly"]

# Optional per-role client_id override keys in secret/redevops/azure-demo/config. Absent → the
# single bootstrap SP is used for every role (documented default).
_CLIENT_ID_KEY: Dict[Role, str] = {
    "deployer": "deployer_client_id",
    "agent": "agent_client_id",
    "readonly": "readonly_client_id",
}


def vault_cli_reader(path: str) -> Dict[str, str]:
    """Read a KV-v2 secret via the `vault` CLI (needs VAULT_ADDR + VAULT_TOKEN in env).

    Returns the inner ``data.data`` map. The value is never logged here; callers must keep it out of
    logs too.
    """
    out = subprocess.run(
        ["vault", "kv", "get", "-format=json", path],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)["data"]["data"]


@dataclass(frozen=True)
class AzureSession:
    """A role-scoped Azure credential + the subscription it targets.

    The Azure analog of a ``boto3.Session``: management SDK clients are built as
    ``SomeMgmtClient(session.credential, session.subscription_id)``. ``credential`` is an
    ``azure-identity`` ``TokenCredential`` that refreshes its own tokens internally.
    """

    credential: Any
    subscription_id: str
    location: str
    role: str = ""

    def __repr__(self) -> str:  # never leak the credential / any token material
        return (
            f"<AzureSession role={self.role or '?'} subscription={self.subscription_id} "
            f"location={self.location} credential=***redacted***>"
        )

    __str__ = __repr__


@dataclass
class AzureDemoCreds:
    """Resolves short-lived, role-scoped ``AzureSession`` objects for the demo subscription.

    Parameters
    ----------
    vault_reader:
        ``path -> {key: value}`` reader. Defaults to the ``vault`` CLI; inject a stub in tests so no
        real Vault (or key material on disk) is needed.
    """

    vault_reader: Callable[[str], Dict[str, str]] = vault_cli_reader
    bootstrap_path: str = "secret/redevops/azure-demo/bootstrap"
    config_path: str = "secret/redevops/azure-demo/config"
    session_ttl: int = 3600  # kept for signature parity; ClientSecretCredential self-refreshes

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    _cache: Dict[str, AzureSession] = field(default_factory=dict, repr=False, compare=False)
    _config: Optional[Dict[str, str]] = field(default=None, repr=False, compare=False)
    _boot: Optional[Dict[str, str]] = field(default=None, repr=False, compare=False)

    # ---- lazy Vault loads (secrets stay inside this object) ----
    def _load_config(self) -> Dict[str, str]:
        if self._config is None:
            self._config = self.vault_reader(self.config_path)
        return self._config

    def _load_bootstrap(self) -> Dict[str, str]:
        if self._boot is None:
            self._boot = self.vault_reader(self.bootstrap_path)
        return self._boot

    # ---- public config surface (non-secret) ----
    @property
    def location(self) -> str:
        cfg = self._load_config()
        return cfg.get("location") or cfg.get("region", "eastus")

    @property
    def subscription_id(self) -> str:
        return self._load_config()["subscription_id"]

    @property
    def tenant_id(self) -> str:
        return self._load_bootstrap()["tenant_id"]

    # ---- the one operation that matters ----
    def session(self, role: Role) -> AzureSession:
        """Return an ``AzureSession`` scoped to *role*.

        Cached per-role (the credential refreshes its own tokens). Raises ``KeyError`` for an unknown
        role. Import of ``azure-identity`` is lazy so this module imports without the SDK installed.
        """
        if role not in _ROLES:
            raise KeyError(f"unknown role {role!r}; expected one of {_ROLES}")
        with self._lock:
            cached = self._cache.get(role)
            if cached is not None:
                return cached

            from azure.identity import ClientSecretCredential  # lazy: keep import optional

            cfg = self._load_config()
            boot = self._load_bootstrap()
            # per-role client_id override if present, else the single bootstrap SP for every role
            client_id = cfg.get(_CLIENT_ID_KEY[role]) or boot["client_id"]
            credential = ClientSecretCredential(
                tenant_id=boot["tenant_id"],
                client_id=client_id,
                client_secret=boot["client_secret"],
            )
            session = AzureSession(
                credential=credential,
                subscription_id=cfg["subscription_id"],
                location=cfg.get("location") or cfg.get("region", "eastus"),
                role=role,
            )
            self._cache[role] = session
            return session

    # ---- never leak key material ----
    def __repr__(self) -> str:
        sub = self._config.get("subscription_id", "?") if self._config else "unloaded"
        loaded = "loaded" if self._boot is not None else "lazy"
        return f"<AzureDemoCreds subscription={sub} roles={_ROLES} bootstrap=***redacted*** ({loaded})>"

    __str__ = __repr__
