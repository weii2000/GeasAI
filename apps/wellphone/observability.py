from __future__ import annotations

import json
import logging
from datetime import UTC, datetime


_LOGGER = logging.getLogger("wellphone")
type LogValue = str | int | float | bool | None


def configure_logging() -> None:
    if not _LOGGER.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        _LOGGER.addHandler(handler)
    _LOGGER.setLevel(logging.INFO)
    _LOGGER.propagate = False


def log_event(event: str, **fields: LogValue) -> None:
    _LOGGER.info(
        json.dumps(
            {
                "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
                "event": event,
                **fields,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
