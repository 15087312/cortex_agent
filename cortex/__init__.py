"""Cortex Agent — 一键启动 Humanoid AGI"""

try:
    from cortex.version import __version__, __version_name__, get_version_string
    __all__ = ["__version__", "__version_name__", "get_version_string"]
except ImportError:
    __version__ = "unknown"
    __version_name__ = "unknown"
    def get_version_string():
        return f"v{__version__}"

