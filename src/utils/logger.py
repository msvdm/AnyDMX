"""Dual-target logger: console + rotating file in logs/."""

import logging
import logging.handlers

from src.utils.paths import logs_dir

_configured = False


def get_logger(name):
    global _configured
    if not _configured:
        root = logging.getLogger("anydmx")
        root.setLevel(logging.INFO)
        fmt = logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s")

        console = logging.StreamHandler()
        console.setFormatter(fmt)
        root.addHandler(console)

        try:
            log_file = logs_dir() / "anydmx.log"
            file_handler = logging.handlers.RotatingFileHandler(
                log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
            file_handler.setFormatter(fmt)
            root.addHandler(file_handler)
        except OSError:
            pass  # read-only location: console logging still works
        _configured = True
    return logging.getLogger(f"anydmx.{name}")
