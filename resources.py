"""Resolve bundled assets independently of the process working directory."""
import sys
from pathlib import Path


def get_resource_path(relative_path: str) -> str:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return str(root / relative_path)
