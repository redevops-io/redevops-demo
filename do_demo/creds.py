"""Vault-backed DigitalOcean credentials for the ReDevOps DO demo.

DO's auth model is much SIMPLER than AWS's: there is no role assumption / STS. Access is a single
account-wide **API token**. Scoping is done by which token you mint in the DO console (read-only vs
read/write), not per-call. To keep every cloud binding uniform we preserve the AWS shape —
``session(role) -> client`` — but the *same* client is returned for every ``role``.

Secrets live in Vault:
    secret/redevops/do-demo/bootstrap  → {api_token: "dop_v1_..."}
    secret/redevops/do-demo/config     → {region: "nyc3", registry_name: "redevops-demo"}

HARD RULE (identical to the AWS binding): the token is NEVER logged, printed, or placed in a
repr/str. ``DOClient.__repr__`` and ``DoDemoCreds.__repr__`` redact it. Tests assert this.
"""
from __future__ import annotations

import json
import subprocess
import threading
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Dict, Literal, Optional

# Kept for uniformity with the AWS binding. DO tokens are NOT role-scoped — the same client backs
# every role; scope your token in the DO console instead.
Role = Literal["deployer", "agent", "readonly"]
_ROLES = ("deployer", "agent", "readonly")


def vault_cli_reader(path: str) -> Dict[str, str]:
    """Read a KV-v2 secret via the `vault` CLI (needs VAULT_ADDR + VAULT_TOKEN in env).

    Returns the inner ``data.data`` map. The token value is never logged here; callers must keep it
    out of logs too.
    """
    out = subprocess.run(
        ["vault", "kv", "get", "-format=json", path],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)["data"]["data"]


class DOClient:
    """Thin authenticated client for the DigitalOcean v2 API.

    Deliberately dependency-free: it prefers the ``requests`` SDK if installed (lazy import) and
    otherwise falls back to stdlib ``urllib``. Exposes ``get(path)`` / ``post(path, body)`` returning
    parsed JSON. The bearer token is held privately and never rendered.
    """

    API = "https://api.digitalocean.com"

    def __init__(self, token: str, *, region: str = "nyc3",
                 registry_name: Optional[str] = None, timeout: int = 15) -> None:
        self._token = token  # secret — never logged / repr'd
        self.region = region
        self.registry_name = registry_name
        self._timeout = timeout

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        url = path if path.startswith("http") else self.API + path
        data = json.dumps(body).encode() if body is not None else None
        try:
            import requests  # lazy SDK import; optional
        except ImportError:
            requests = None  # type: ignore[assignment]
        if requests is not None:
            resp = requests.request(method, url, data=data, headers=self._headers(), timeout=self._timeout)
            resp.raise_for_status()
            return resp.json() if resp.content else {}
        req = urllib.request.Request(url, data=data, method=method, headers=self._headers())
        with urllib.request.urlopen(req, timeout=self._timeout) as r:  # noqa: S310 (fixed https host)
            raw = r.read()
        return json.loads(raw) if raw else {}

    def get(self, path: str) -> dict:
        return self._request("GET", path)

    def post(self, path: str, body: Optional[dict] = None) -> dict:
        return self._request("POST", path, body)

    def __repr__(self) -> str:
        return f"<DOClient region={self.region} registry={self.registry_name} token=***redacted***>"

    __str__ = __repr__


@dataclass
class DoDemoCreds:
    """Resolves a DigitalOcean API client from Vault-held config.

    Parameters
    ----------
    vault_reader:
        ``path -> {key: value}`` reader. Defaults to the ``vault`` CLI; inject a stub in tests so no
        real Vault (or token on disk) is needed.
    """

    vault_reader: Callable[[str], Dict[str, str]] = vault_cli_reader
    bootstrap_path: str = "secret/redevops/do-demo/bootstrap"
    config_path: str = "secret/redevops/do-demo/config"

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    _client: Optional[DOClient] = field(default=None, repr=False, compare=False)
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
    def region(self) -> str:
        return self._load_config().get("region", "nyc3")

    @property
    def registry_name(self) -> Optional[str]:
        return self._load_config().get("registry_name")

    # ---- the one operation that matters ----
    def session(self, role: Role) -> DOClient:
        """Return a ``DOClient`` for *role*. DO tokens aren't role-scoped, so the SAME client backs
        every role (kept for parity with the AWS assume-role factory). Raises ``KeyError`` for an
        unknown role."""
        if role not in _ROLES:
            raise KeyError(f"unknown role {role!r}; expected one of {list(_ROLES)}")
        with self._lock:
            if self._client is not None:
                return self._client
            cfg = self._load_config()
            boot = self._load_bootstrap()
            self._client = DOClient(
                boot["api_token"],
                region=cfg.get("region", "nyc3"),
                registry_name=cfg.get("registry_name"),
            )
            return self._client

    # ---- never leak the token ----
    def __repr__(self) -> str:
        reg = self._config.get("region", "?") if self._config else "unloaded"
        loaded = "loaded" if self._boot is not None else "lazy"
        return f"<DoDemoCreds region={reg} roles={list(_ROLES)} token=***redacted*** ({loaded})>"

    __str__ = __repr__
