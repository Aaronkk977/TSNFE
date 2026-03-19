"""
Retry decorator and utilities using tenacity library
Handles exponential backoff and custom retry logic
"""

from typing import Callable, Optional, Type, Union

from tenacity import (
    after_log,
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .logging import logger


def retry_with_backoff(
    max_attempts: int = 3,
    backoff_base: int = 1,
    backoff_max: int = 60,
    exceptions: Union[Type[Exception], tuple] = Exception,
    logger_instance: Optional[object] = None,
):
    """
    Decorator for automatic retry with exponential backoff.

    Args:
        max_attempts: Maximum number of retry attempts
        backoff_base: Initial backoff in seconds
        backoff_max: Maximum backoff in seconds
        exceptions: Exception(s) to catch for retry
        logger_instance: Logger instance for logging (default: loguru logger)

    Returns:
        Decorated function with retry capability

    Example:
        @retry_with_backoff(max_attempts=3)
        def fetch_data():
            response = requests.get("https://api.example.com/data")
            response.raise_for_status()
            return response.json()
    """

    log_func = logger_instance or logger

    def decorator(func: Callable) -> Callable:
        return retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(
                multiplier=1,
                min=backoff_base,
                max=backoff_max,
            ),
            retry=retry_if_exception_type(exceptions),
            before_sleep=before_sleep_log(log_func, log_level="WARNING"),
            after=after_log(log_func, log_level="INFO"),
            reraise=True,
        )(func)

    return decorator


class RetryConfig:
    """Configuration for retry behavior."""

    def __init__(
        self,
        max_attempts: int = 3,
        backoff_base: int = 1,
        backoff_max: int = 60,
    ):
        self.max_attempts = max_attempts
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max

    def get_decorator(self, exceptions=Exception):
        """Get retry decorator with this config."""
        return retry_with_backoff(
            max_attempts=self.max_attempts,
            backoff_base=self.backoff_base,
            backoff_max=self.backoff_max,
            exceptions=exceptions,
        )


# Common retry configurations
DEFAULT_RETRY_CONFIG = RetryConfig(
    max_attempts=3,
    backoff_base=1,
    backoff_max=60,
)

AGGRESSIVE_RETRY_CONFIG = RetryConfig(
    max_attempts=5,
    backoff_base=2,
    backoff_max=120,
)

CONSERVATIVE_RETRY_CONFIG = RetryConfig(
    max_attempts=1,
    backoff_base=0,
    backoff_max=0,
)
