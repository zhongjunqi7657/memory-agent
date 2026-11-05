"""Redact complete formatted records, including exception tracebacks."""

import logging
from logging.handlers import RotatingFileHandler

from sqlalchemy.engine import make_url

from app.security.redaction import redact_secrets


def configure_logging(settings, *, name="runtime"):
    class SafeFormatter(logging.Formatter):
        def format(self, record):
            value = super().format(record)
            for secret in (settings.local_api_token, settings.dashscope_api_key,
                           settings.demo_shared_password, make_url(settings.database_url).password):
                if secret:
                    value = value.replace(secret, "[REDACTED]")
            return redact_secrets(value).text

    directory = settings.app_data_dir / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(directory / f"{name}.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(SafeFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.propagate = True
    return handler
