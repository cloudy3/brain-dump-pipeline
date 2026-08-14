"""Structured logging that deliberately excludes personal message content."""

import json
import logging
from datetime import UTC, datetime
from typing import ClassVar


class JsonFormatter(logging.Formatter):
    """Format log records as compact JSON with an allowlist of context fields."""

    context_fields: ClassVar[tuple[str, ...]] = (
        "operation",
        "request_id",
        "update_id",
        "message_id",
        "state",
        "status_code",
        "latency_ms",
        "error_type",
        "persistence_status",
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in self.context_fields:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        return json.dumps(payload, separators=(",", ":"), default=str)


def configure_logging(level: str) -> None:
    """Configure application logs once at process startup."""

    root_logger = logging.getLogger()
    handler = next(
        (item for item in root_logger.handlers if item.get_name() == "brain_dump_json"),
        None,
    )
    if handler is None:
        handler = logging.StreamHandler()
        handler.set_name("brain_dump_json")
        handler.setFormatter(JsonFormatter())
        root_logger.addHandler(handler)
    root_logger.setLevel(level)

    # Telegram tokens are embedded in Bot API URLs, so suppress request-level URL logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
