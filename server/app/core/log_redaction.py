"""Keep personal data out of the server logs.

uvicorn's access log prints each request's full path, query string included, and query
strings can hold guest names, emails or phone numbers (GET /api/guests?q=...). The
filter below drops the query string from every access log line.
"""

import logging


class DropQueryStrings(logging.Filter):
    """For uvicorn.access records: args are (client, method, path, http version, status)."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            path, _, query = args[2].partition("?")
            if query:
                record.args = (*args[:2], f"{path}?[redacted]", *args[3:])
        return True


def install_access_log_redaction() -> None:
    """Idempotent: apps are created many times in tests."""
    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, DropQueryStrings) for f in access_logger.filters):
        access_logger.addFilter(DropQueryStrings())
