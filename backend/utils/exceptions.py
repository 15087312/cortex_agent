"""
Custom exception types.
"""


class CortexError(Exception):
    """Base exception for Cortex."""


class ModelError(CortexError):
    """Model API call failed."""


class MemoryError(CortexError):
    """Memory system operation failed."""


class ConfigError(CortexError):
    """Configuration error."""
