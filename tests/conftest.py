"""Suite-wide guard: the test suite must stay fully offline."""

from __future__ import annotations

import socket

import pytest

from tests._offline import block


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any real name resolution or packet send fail loudly."""

    monkeypatch.setattr(socket.socket, "connect", block)
    monkeypatch.setattr(socket.socket, "connect_ex", block)
    monkeypatch.setattr(socket.socket, "sendto", block)
    monkeypatch.setattr(socket.socket, "sendmsg", block)
    monkeypatch.setattr(socket, "getaddrinfo", block)
    monkeypatch.setattr(socket, "gethostbyname", block)
    monkeypatch.setattr(socket, "gethostbyname_ex", block)
    monkeypatch.setattr(socket, "gethostbyaddr", block)
