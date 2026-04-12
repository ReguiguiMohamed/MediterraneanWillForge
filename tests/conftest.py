"""
tests/conftest.py
─────────────────
Ensures the repo root is on sys.path so all test modules can import
from `data.*` and `ingestion.*` without installing the package.
"""

import sys
from pathlib import Path

# Insert repo root (parent of tests/) at the front of sys.path
_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
