"""Structured (JSON) operational logging — stdout, not persisted.

Distinct from shared/audit.py, which writes a durable compliance trail to
Postgres. This is for local/aggregator log tailing (docker logs, journalctl,
a log shipper): one JSON object per line, with `correlation_id` as a
standard field so a single pipeline run's log lines can be grepped/joined
across the 5 agent processes + the orchestrator.
"""
import json
import logging
import os
import sys

_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in ("correlation_id", "agent", "event_type", "duration_ms"):
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def get_logger(name: str) -> logging.Logger:
    """Returns a module-level logger emitting one JSON object per line to
    stdout. Safe to call repeatedly (e.g. at module import in every agent) —
    handlers are only attached once per logger name."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(_LEVEL)
        logger.propagate = False
    return logger
