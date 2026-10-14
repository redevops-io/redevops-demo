import os
import pathlib
import sys

import pytest

# The real infra operator lives in the (separate) agentic-os repo — apps/infra, package `infra`.
# We consume it as a dependency. Default assumes agentic-os is checked out alongside this repo;
# override with AGENTIC_OS_APPS if it lives elsewhere. Tests skip cleanly if it isn't present.
_DEFAULT_APPS = pathlib.Path(__file__).resolve().parents[2] / "agentic-os" / "apps"
if os.environ.get("AGENTIC_OS_ROOT"):
    _DEFAULT_APPS = pathlib.Path(os.environ["AGENTIC_OS_ROOT"]) / "apps"   # same resolution as missions/deploy_operate.py
_AGENTIC_OS_APPS = os.environ.get("AGENTIC_OS_APPS", str(_DEFAULT_APPS))


@pytest.fixture(scope="session")
def infra_core():
    if not pathlib.Path(_AGENTIC_OS_APPS, "infra", "core.py").exists():
        pytest.skip("agentic-os infra operator not found (set AGENTIC_OS_APPS)")
    if _AGENTIC_OS_APPS not in sys.path:
        sys.path.insert(0, _AGENTIC_OS_APPS)
    import infra.core as core  # noqa: E402

    return core


@pytest.fixture(autouse=True)
def sim_plan_root(tmp_path, monkeypatch):
    """Point the kernel infra operator at a throwaway terraform root so the sim runner can write the saved
    plan file. Kernel >= v0.3.26 binds the provision approval to the sha256 of that file (`plan_digest`) and
    refuses to apply without it; the canned runner in missions/deploy_operate.py writes it under -chdir."""
    if not pathlib.Path(_AGENTIC_OS_APPS, "infra", "core.py").exists():
        yield
        return
    if _AGENTIC_OS_APPS not in sys.path:
        sys.path.insert(0, _AGENTIC_OS_APPS)
    import infra.core as core  # noqa: E402

    root = tmp_path / "deploy"
    (root / "terraform" / "envs" / "aws").mkdir(parents=True)
    monkeypatch.setattr(core, "TF_ROOT", str(root / "terraform"))
    yield
