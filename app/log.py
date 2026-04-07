import json
import logging
import time

logger = logging.getLogger("janice")


def log_event(event: str, **kwargs) -> None:
    """Emit a structured log line as JSON."""
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event, **kwargs}
    logger.info(json.dumps(record))
