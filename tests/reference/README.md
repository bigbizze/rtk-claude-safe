# Python reference

This directory freezes the Python implementation from commit `2e8a20f` for migration
verification. It is test data, not a runtime dependency or an installable application.
Do not update the reference to make a failing Go comparison pass.

Run `python3 tools/differential.py` from the repository root to compare Go command
classification and payload handling with this reference. The script uses only Python's
standard library. Go tests use committed golden fixtures and do not require Python.

The original Python tests are retained to document the behavior being ported. To run them,
install pytest in a development environment and set `PYTHONPATH=tests/reference`.
