# src/tw_analyst_pipeline/__init__.py
"""
Taiwan Analyst Signal Pipeline
==============================

A production-grade quantitative trading data pipeline that extracts 
stock buy/sell signals from Taiwan stock analyst YouTube videos.

Version: 0.1.0
"""

__version__ = "0.1.0"
__author__ = "Quant Team"
__email__ = "info@example.com"

from .utils.config import Settings, load_config
from .utils.logging import setup_logging

__all__ = [
    "Settings",
    "load_config",
    "setup_logging",
]
