"""Cortex Agent — FastAPI backend entry point."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve()))

from backend.main import main

if __name__ == "__main__":
    main()
