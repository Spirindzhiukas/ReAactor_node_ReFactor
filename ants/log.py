"""Scoped logging for the nodepack.

Deliberately does NOT monkeypatch the global ``logging`` module (no custom
module-level functions, no attribute overrides): other custom nodes that do
the same have historically collided. Only ``logging.addLevelName`` is used,
which is a benign global name registry.
"""

import copy
import logging
import sys

# A level between INFO (20) and WARNING (30), used for progress chatter.
STATUS_LEVEL = 25
logging.addLevelName(STATUS_LEVEL, "STATUS")

_COLORS = {
    "DEBUG": "\033[0;36m",
    "STATUS": "\033[38;5;173m",
    "INFO": "\033[0;32m",
    "WARNING": "\033[0;33m",
    "ERROR": "\033[0;31m",
    "CRITICAL": "\033[0;37;41m",
    "RESET": "\033[0m",
}


class _ColoredFormatter(logging.Formatter):
    def format(self, record):
        colored = copy.copy(record)
        seq = _COLORS.get(colored.levelname, _COLORS["RESET"])
        colored.levelname = f"{seq}{colored.levelname}{_COLORS['RESET']}"
        return super().format(colored)


class ReFactorLogger(logging.Logger):
    """Logger with the project's traditional ``status`` level (between INFO and WARNING)."""

    def status(self, message, *args, **kwargs):
        if self.isEnabledFor(STATUS_LEVEL):
            self._log(STATUS_LEVEL, message, args, **kwargs)


def _make_logger(name: str) -> logging.Logger:
    # Temporarily swap the global logger class only for OUR logger name —
    # never left installed (other custom nodes must be unaffected).
    previous_class = logging.getLoggerClass()
    logging.setLoggerClass(ReFactorLogger)
    try:
        log = logging.getLogger(name)
    finally:
        logging.setLoggerClass(previous_class)
    log.propagate = False
    if not log.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            _ColoredFormatter("[%(name)s] %(asctime)s - %(levelname)s - %(message)s", datefmt="%H:%M:%S")
        )
        log.addHandler(handler)
    log.setLevel(logging.INFO)
    return log


logger = _make_logger("ReFactor")

_LEVEL_MAP = {0: logging.WARNING, 1: STATUS_LEVEL, 2: logging.DEBUG}


def set_console_level(level: int) -> None:
    """Map the node's console_log_level socket (0/1/2) to a logger level."""
    logger.setLevel(_LEVEL_MAP.get(int(level), STATUS_LEVEL))
