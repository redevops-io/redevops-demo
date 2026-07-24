"""DigitalOcean binding for the ReDevOps multi-cloud demo (DOKS + DOCR).

Mirrors aws_demo/ on DigitalOcean: Vault-held API token (creds), preflight/doctor built on
demo_common, and a Billing-API budget guard. DO has no role-assumption or spot analog, and one
account-global Container Registry — see the module docstrings for details.
"""
