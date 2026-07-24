"""``python -m demo_common.doctor <cloud>`` — run one cloud's deployment-preflight checklist.

A thin dispatcher over the per-cloud bindings so the cockpit / scripts have a single uniform entry
point across hyperscalers. Each cloud package owns its own ``doctor.main()`` (governed Vault creds with
an ambient fallback); this only routes to it, importing lazily so selecting one cloud never requires
another cloud's SDK.

    python -m demo_common.doctor aws
    python -m demo_common.doctor azure
    python -m demo_common.doctor gcp
    python -m demo_common.doctor digitalocean   # or: do
"""
from __future__ import annotations

import importlib
import sys
from typing import List, Optional

_MODULES = {
    "aws": "aws_demo.doctor",
    "azure": "azure_demo.doctor",
    "gcp": "gcp_demo.doctor",
    "do": "do_demo.doctor",
    "digitalocean": "do_demo.doctor",
}


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    clouds = "|".join(sorted(set(k for k in _MODULES if k != "do")))
    if not argv or argv[0] not in _MODULES:
        print(f"usage: python -m demo_common.doctor <{clouds}>")
        return 2
    module = importlib.import_module(_MODULES[argv[0]])
    return int(module.main())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
