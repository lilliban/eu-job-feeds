"""Connector registry.

`CONNECTORS` holds the single-slug providers. Workday is deliberately absent: it
is identified by a tenant/datacentre/site triple rather than a slug, so it has
its own type and its own code path (see `workday.py`).
"""

from __future__ import annotations

from .ashby import AshbyConnector
from .base import Connector, ProbeResult
from .greenhouse import GreenhouseConnector
from .lever import LeverConnector
from .personio import PersonioConnector
from .recruitee import RecruiteeConnector
from .smartrecruiters import SmartRecruitersConnector
from .workable import WorkableConnector
from .workday import WorkdayConnector, WorkdayTarget

#: Discovery probes providers in this order: the ones that answer with a clean
#: 404 come first, SmartRecruiters last because it can only ever say AMBIGUOUS.
CONNECTORS: dict[str, Connector] = {
    c.provider: c
    for c in (
        GreenhouseConnector(),
        LeverConnector(),
        AshbyConnector(),
        RecruiteeConnector(),
        WorkableConnector(),
        PersonioConnector(),
        SmartRecruitersConnector(),
    )
}

#: Every value that can appear in `source_board`.
PROVIDERS: tuple[str, ...] = tuple(CONNECTORS) + (WorkdayConnector.provider,)

__all__ = [
    "CONNECTORS",
    "PROVIDERS",
    "AshbyConnector",
    "Connector",
    "GreenhouseConnector",
    "LeverConnector",
    "PersonioConnector",
    "ProbeResult",
    "RecruiteeConnector",
    "SmartRecruitersConnector",
    "WorkableConnector",
    "WorkdayConnector",
    "WorkdayTarget",
]
