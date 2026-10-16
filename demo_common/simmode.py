"""Single parser for the SIM switch: only an exact "0" selects real mode; anything else simulates."""
from __future__ import annotations

import logging
import os

_log = logging.getLogger(__name__)


def sim_enabled(environ=None) -> bool:
    raw = (os.environ if environ is None else environ).get("SIM")
    if raw is None or raw == "1":
        return True
    if raw == "0":
        return False
    _log.warning("unrecognised SIM value %r; running in simulation (only SIM=0 selects real mode)", raw)
    return True
