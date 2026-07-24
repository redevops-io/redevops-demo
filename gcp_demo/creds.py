"""Vault → service-account credentials for the ReDevOps GCP demo.

GCP analog of aws_demo/creds.py. AWS's security boundary is STS role-assumption from a single
bootstrap key; GCP's equivalent here is **service-account key material read from Vault**, handed
out as short-lived, scoped ``google.oauth2.service_account.Credentials`` per task:

    deployer  → Terraform / GKE / Artifact Registry provisioning
    agent     → Vertex AI / runtime tool access
    readonly  → monitoring · Cloud Billing · Cloud Monitoring · Security reads

Each role maps to a service-account key stored in Vault; a single SA can back all three (with a
note) or you can bind three least-privilege SAs. Either way the credential is scoped per task.

HARD RULE: key material (the SA private key JSON, the OAuth access token) is NEVER logged,
printed, or placed in a repr/str. Tests assert this.
"""
from __future__ import annotations

import json
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Literal, Optional

Role = Literal["deployer", "agent", "readonly"]

# config keys in secret/redevops/gcp-demo/config that name each role's SA-key Vault field.
# The bootstrap secret holds the actual SA-key JSON blob(s); config points at which one to use.
_KEY_FIELD: Dict[Role, str] = {
    "deployer": "deployer_sa_key",
    "agent": "agent_sa_key",
    "readonly": "readonly_sa_key",
}

# Default scope for the handed-out credentials. cloud-platform keeps the demo simple; a real
# deployment would narrow per role. The scope governs the OAuth token, not the key material.
_DEFAULT_SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)


def vault_cli_reader(path: str) -> Dict[str, str]:
    """Read a KV-v2 secret via the `vault` CLI (needs VAULT_ADDR + VAULT_TOKEN in env).

    Returns the inner `data.data` map. The value is never logged here; callers must
    keep it out of logs too.
    """
    out = subprocess.run(
        ["vault", "kv", "get", "-format=json", path],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)["data"]["data"]


@dataclass
class GcpDemoCreds:
    """Resolves scoped ``service_account.Credentials`` for the demo project.

    Parameters
    ----------
    vault_reader:
        ``path -> {key: value}`` reader. Defaults to the ``vault`` CLI; inject a stub
        in tests so no real Vault (or key material on disk) is needed.
    scopes:
        OAuth scopes requested for every handed-out credential.
    """

    vault_reader: Callable[[str], Dict[str, str]] = vault_cli_reader
    bootstrap_path: str = "secret/redevops/gcp-demo/bootstrap"
    config_path: str = "secret/redevops/gcp-demo/config"
    scopes: tuple = _DEFAULT_SCOPES

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    _cache: Dict[Role, Any] = field(default_factory=dict, repr=False, compare=False)
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
    def project_id(self) -> str:
        return self._load_config()["project_id"]

    @property
    def region(self) -> str:
        return self._load_config().get("region", "us-central1")

    def key_field(self, role: Role) -> str:
        """Which bootstrap field holds this role's SA key (config may remap the default)."""
        return self._load_config().get(_KEY_FIELD[role], _KEY_FIELD[role])

    # ---- the one operation that matters ----
    def credentials(self, role: Role):
        """Return ``service_account.Credentials`` scoped for *role*, built from the Vault SA key.

        Cached per-role. Raises ``KeyError`` for an unknown role. The SDK is imported lazily so
        this module imports without ``google-auth`` installed.
        """
        if role not in _KEY_FIELD:
            raise KeyError(f"unknown role {role!r}; expected one of {list(_KEY_FIELD)}")

        # lazy import: package must import without the google SDK present
        from google.oauth2 import service_account  # type: ignore

        with self._lock:
            cached = self._cache.get(role)
            if cached is not None:
                return cached

            cfg = self._load_config()
            boot = self._load_bootstrap()
            field_name = cfg.get(_KEY_FIELD[role], _KEY_FIELD[role])
            raw = boot[field_name]
            info = json.loads(raw) if isinstance(raw, str) else raw

            creds = service_account.Credentials.from_service_account_info(
                info, scopes=list(self.scopes)
            )
            # bind the project so downstream clients don't have to be told twice
            project = cfg.get("project_id")
            if project and hasattr(creds, "with_quota_project"):
                try:
                    creds = creds.with_quota_project(project)
                except Exception:
                    pass
            self._cache[role] = creds
            return creds

    # ---- never leak key material ----
    def __repr__(self) -> str:
        proj = self._config.get("project_id", "?") if self._config else "unloaded"
        loaded = "loaded" if self._boot is not None else "lazy"
        return f"<GcpDemoCreds project={proj} roles={list(_KEY_FIELD)} sa_key=***redacted*** ({loaded})>"

    __str__ = __repr__


def session_factory(creds: Optional[GcpDemoCreds] = None) -> Callable[[Role], Any]:
    """Return ``role -> service_account.Credentials`` — the GCP analog of AwsDemoCreds.session.

    Named to mirror the AWS ``session_factory(role) -> session`` shape the preflight expects.
    """
    creds = creds or GcpDemoCreds()
    return creds.credentials
