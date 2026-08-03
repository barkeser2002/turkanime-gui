"""Sürümün TEK kaynağı.

`cli/version.py`, `gui/__init__.py` ve `common/updater.py` buradan okur;
`pyproject.toml` ise poetry'nin literal istemesi yüzünden ayrı durur ve CI
release'te tag'den senkronlanır (`tests/test_qt_updates.py` ikisinin
uyuştuğunu denetliyor).
"""
__version__ = "10.0.0"
