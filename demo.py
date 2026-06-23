"""Compatibility wrapper for running all demo scenarios.

Prefer:
    python -m demo

The historical command still works:
    python demo.py
"""

from demo.__main__ import main


if __name__ == "__main__":
    main()