"""Compat helpers for the vendored GFPGAN/CodeFormer archs.

Replaces the ``r_basicsr.utils`` imports those arch files use (only
``get_root_logger`` is actually referenced at runtime).
"""

import logging


def get_root_logger(logger_name="ants", log_level=logging.INFO):
    log = logging.getLogger(logger_name)
    if not log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
        log.addHandler(handler)
    log.setLevel(log_level)
    return log


def get_logger(logger_name="ants"):
    return get_root_logger(logger_name)


def make_logger(logger_name="ants", log_level=logging.INFO):
    return get_root_logger(logger_name, log_level)
