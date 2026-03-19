"""
Logging configuration using loguru
Provides structured logging with file and console outputs
"""

import sys
from pathlib import Path
from typing import Optional

from loguru import logger

# Remove default handler
logger.remove()


def setup_logging(
    level: str = "INFO",
    log_dir: str = "./logs",
    to_console: bool = True,
    to_file: bool = True,
    json_format: bool = False,
) -> logger:
    """
    Configure loguru logger with console and file outputs.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_dir: Directory for log files
        to_console: Enable console output
        to_file: Enable file output
        json_format: Use JSON format (useful for log aggregation)

    Returns:
        Configured logger object
    """

    # Create log directory
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    # Console format
    console_format = (
        "<green>[{time:YYYY-MM-DD HH:mm:ss}]</green> <level>{level: <8}</level> "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
    )

    # File format (more detailed)
    file_format = (
        "[{time:YYYY-MM-DD HH:mm:ss}] <level>{level: <8}</level> "
        "[{process}] {name}:{function}:{line} - {message}"
    )

    # JSON format
    json_fmt = '{{time: {time:YYYY-MM-DD HH:mm:ss}, level: {level}, module: {name}, function: {function}, line: {line}, message: {message}}}'

    # Add console handler
    if to_console:
        logger.add(
            sys.stdout,
            format=console_format,
            level=level,
            colorize=True,
            backtrace=True,
            diagnose=True,
        )

    # Add file handler
    if to_file:
        log_file = log_path / f"pipeline.log"
        logger.add(
            str(log_file),
            format=file_format if not json_format else json_fmt,
            level=level,
            rotation="daily",  # Rotate daily
            retention="7 days",  # Keep 7 days of logs
            compression="gz",  # Compress rotated logs
            backtrace=True,
            diagnose=False,  # Disable diagnose to reduce file size
        )

    return logger


class LoggerMixin:
    """
    Mixin class that provides logging capability to any class.

    Usage:
        class MyClass(LoggerMixin):
            def my_method(self):
                self.logger.info("This is an info message")
    """

    @property
    def logger(self):
        """Get logger instance for this class."""
        if not hasattr(self, "_logger"):
            self._logger = logger.bind(
                component=self.__class__.__name__,
            )
        return self._logger


# Context variables for structured logging
class LogContext:
    """Context manager for structured logging."""

    def __init__(self, video_id: Optional[str] = None, **kwargs):
        self.video_id = video_id
        self.context = kwargs

    def __enter__(self):
        """Enter context and bind values to logger."""
        bind_dict = {}
        if self.video_id:
            bind_dict["video_id"] = self.video_id
        bind_dict.update(self.context)
        self.token = logger.contextualize(**bind_dict)
        self.token.__enter__()
        return self

    def __exit__(self, *args):
        """Exit context and unbind values."""
        self.token.__exit__(*args)


# Configure default logger instance
logger = setup_logging(
    level="INFO",
    to_console=True,
    to_file=True,
)
