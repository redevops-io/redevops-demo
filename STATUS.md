# Status

- **Maturity:** simulation-only
- **Version:** 0.0.0 (pyproject); no tags or releases
- **Last git activity on the default branch:** 2026-10-14 (from `git log -1 --format=%cs`; the working clone is shallow)

## What is supported
- Simulated deploy-and-operate, harden and incident missions (SIM=1, $0)
- Local test suite (pytest, no cloud calls) and CI in .github/workflows/tests.yml
- Isolated-demo auto-approval driver against simulators (see README)

## What is not supported / not claimed
- Real cloud apply on any of AWS/Azure/GCP/DigitalOcean (listed as Next in the README)
- Any claim of a live or self-healing EKS stack from this repo
- Production use

## Known issues
- GitHub repo description says it provisions a real EKS stack; the README and this status say everything is simulated
- Port: scripts/up.sh and root compose.yml serve the cockpit on :8080, deploy/compose.demo.yml on :8000
- Figures (68 resources, ~$0.31/hr, 18/26/34 tests) are illustrative/historical and not backed by a committed report
- docs/aws-fit-roadmap.md is aspirational; its 'production runtime' and '80-90%' statements are unsupported
- Licence label differs from sibling repos (AGPL-3.0-only here): counsel item, not edited

No quantitative claim in this repo has been externally reproduced or field validated.
