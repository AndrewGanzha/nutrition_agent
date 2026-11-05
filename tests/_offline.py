"""Shared pieces of the suite-wide offline guard."""

from __future__ import annotations


class NetworkAccessBlocked(BaseException):
    """A test tried to touch the network.

    Deliberately a ``BaseException``: sinks and the agent catch ``Exception``
    around their own calls and would turn a swallowed guard error into an
    ordinary ``failed`` item, hiding the real problem.
    """


def block(*args: object, **kwargs: object) -> None:
    raise NetworkAccessBlocked("network access is disabled in tests")


__all__ = ["NetworkAccessBlocked", "block"]
