"""The demo compose carries what the kernel needs to run it, so the documented stack starts and its cockpit can finish a deploy.

Each of these was missing at some point and left the stack unusable while every unit test passed: no control token (503 everywhere), an
operator skipped at start, untrusted federated operators (nothing could be planned), no writable plan directory (no plan digest, so no
approval could ever apply), no explicit profile (the Sidekick image refuses to start).
"""
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
C = yaml.safe_load((ROOT / "deploy" / "compose.demo.yml").read_text())["services"]
OPERATORS = ("infra", "edge-sentinel", "operate", "agentic-compliance", "agentic-privacy")


def test_sidekick_and_every_operator_require_the_control_token_and_the_approval_secret():
    for name in OPERATORS + ("sidekick",):
        env = C[name]["environment"]
        for var in ("AGENTIC_OS_CONTROL_TOKEN", "AGENTIC_OS_APPROVAL_SECRET"):
            assert str(env[var]).startswith("${" + var + ":?"), f"{name}: {var} must be required, never defaulted"


def test_sidekick_requires_an_explicit_profile_and_demo_env_writes_one():
    assert str(C["sidekick"]["environment"]["AGENTIC_OS_PROFILE"]).startswith("${AGENTIC_OS_PROFILE:?")
    assert "AGENTIC_OS_PROFILE=development" in (ROOT / "scripts" / "demo-env.sh").read_text()


def test_sidekick_waits_for_every_operator_to_be_healthy():
    deps = C["sidekick"]["depends_on"]
    assert set(deps) == set(OPERATORS) and all(v == {"condition": "service_healthy"} for v in deps.values())
    assert all("healthcheck" in C[o] for o in OPERATORS)


def test_exactly_the_federated_operators_are_trusted():
    modules = yaml.safe_load((ROOT / "operators" / "modules.yaml").read_text())["operators"]
    trusted = {l.strip() for l in (ROOT / "operators" / "trusted-sources").read_text().splitlines() if l.strip()}
    assert trusted == {f"http:{name}" for name in modules} and set(modules) == set(OPERATORS)
    assert C["sidekick"]["environment"]["AGENTIC_OS_TRUST_FILE"] == "/app/trusted-sources"
    assert any(str(v).endswith(":/app/trusted-sources:ro") for v in C["sidekick"]["volumes"])


def test_the_infra_operator_plans_into_a_writable_copy_of_the_terraform_tree():
    infra = C["infra"]
    assert infra["environment"]["INFRA_DEPLOY_ROOT"] == "/work/infra" and "/work" in infra["tmpfs"]
    assert "cp -R /app/infra/terraform /work/infra/terraform" in " ".join(infra["command"])
    assert (ROOT / "infra" / "terraform" / "envs" / "aws").is_dir()
