"""The interface every check implements."""

from __future__ import annotations

from pcbtester.context import ProjectContext, RunOptions
from pcbtester.models import CheckResult


class Check:
    """One pre-production check.

    Subclasses set ``id``, ``title``, ``description`` and ``default_enabled``,
    and implement :meth:`run`. A check must stand alone: the runner does not
    promise that another check has already executed.
    """

    id: str
    title: str
    description: str
    default_enabled: bool = True

    def run(self, ctx: ProjectContext, options: RunOptions) -> CheckResult:
        raise NotImplementedError
