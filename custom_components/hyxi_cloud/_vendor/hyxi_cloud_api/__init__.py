"""Initialization module for HYXi Cloud API (vendored fork)."""

from .api import HyxiApiClient, HyxiControlError

__version__ = "1.1.5+vendor"
__all__ = ["HyxiApiClient", "HyxiControlError"]
