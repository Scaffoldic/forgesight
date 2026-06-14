"""Smoke test: the contract package imports and reports a version."""

import forgesight_api


def test_version_is_exposed() -> None:
    assert forgesight_api.__version__
    assert isinstance(forgesight_api.__version__, str)
