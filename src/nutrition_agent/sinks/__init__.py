"""Sink implementations and the sink contract."""

from nutrition_agent.sinks.base import ItemStatus, ItemWrite, Sink, SinkResult
from nutrition_agent.sinks.fake import FakeSink, SinkCall
from nutrition_agent.sinks.google_health import GoogleHealthSink, TokenProvider
from nutrition_agent.sinks.sqlite import LocalSQLiteSink

__all__ = [
    "FakeSink",
    "GoogleHealthSink",
    "ItemStatus",
    "ItemWrite",
    "LocalSQLiteSink",
    "Sink",
    "SinkCall",
    "SinkResult",
    "TokenProvider",
]
