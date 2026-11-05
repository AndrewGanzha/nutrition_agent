"""The offline guard itself must stay effective."""

from __future__ import annotations

import socket

import pytest

from tests._offline import NetworkAccessBlocked


def test_tcp_connect_is_blocked() -> None:
    with socket.socket() as sock, pytest.raises(NetworkAccessBlocked):
        sock.connect(("127.0.0.1", 9))


def test_dns_resolution_is_blocked() -> None:
    with pytest.raises(NetworkAccessBlocked):
        socket.getaddrinfo("example.com", 443)
