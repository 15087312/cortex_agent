"""Windows autostart management via registry."""
import sys
import winreg
from pathlib import Path

# Project root: backend/config/autostart.py -> parents[2] = project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]

REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
REG_NAME = "CortexAgent"


def set_autostart(enable: bool) -> bool:
    """Enable or disable autostart on Windows via HKCU Run registry key.

    Adds/removes a registry entry that launches the Qt desktop app on login.
    Returns True on success, False on failure (non-Windows or permission issue).
    Never raises exceptions.
    """
    if sys.platform != "win32":
        return False

    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_SET_VALUE
        )
        if enable:
            # Use pythonw.exe (no console window) with main.py
            pythonw = sys.executable.replace("python.exe", "pythonw.exe")
            main_py = PROJECT_ROOT / "main.py"
            winreg.SetValueEx(
                key, REG_NAME, 0, winreg.REG_SZ,
                f'"{pythonw}" "{main_py}"'
            )
        else:
            try:
                winreg.DeleteValue(key, REG_NAME)
            except FileNotFoundError:
                pass  # already removed, that's fine
        winreg.CloseKey(key)
        return True
    except OSError:
        return False
