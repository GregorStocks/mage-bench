"""Smoke tests for the `magebench` package import path."""

import importlib


def test_magebench_package_is_importable() -> None:
    module = importlib.import_module("magebench")
    assert module.__name__ == "magebench"


def test_magebench_subpackage_is_importable() -> None:
    module = importlib.import_module("magebench.game")
    assert module.__name__ == "magebench.game"
