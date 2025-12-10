import logging


class ProfessionalFormatter(logging.Formatter):
    def __init__(self):
        super().__init__(
            fmt="%(asctime)s | %(shortlevel)-3s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        self.shortmap = {
            "DEBUG": "DBG",
            "INFO": "INF",
            "WARNING": "WRN",
            "ERROR": "ERR",
            "CRITICAL": "CRT",
        }

    def format(self, record) -> str:
        record.shortlevel = self.shortmap.get(record.levelname, "???")
        return super().format(record)


class ShortLevelFormatter(logging.Formatter):
    """Custom formatter to use the first character of the logging level name."""

    def __init__(self):
        super().__init__(
            fmt="%(asctime)s [%(shortlevel)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    def format(self, record) -> str:
        # Replace levelname with its first character (e.g., 'INFO' -> 'I')
        record.shortlevel = record.levelname[0]
        return super().format(record)


def configure_logging(level=logging.WARNING) -> logging.Logger:
    """Set up logging configuration with short level names and return a logger."""
    handler = logging.StreamHandler()
    handler.setFormatter(ProfessionalFormatter())

    root_logger = logging.getLogger()
    if not root_logger.handlers:
        root_logger.setLevel(level)
        root_logger.addHandler(handler)
        # Prevent double logging if handlers are added multiple times
        root_logger.propagate = False
    return root_logger
