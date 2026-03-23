"""Tests for harness epoch constants."""

from magebench.game.harness_epoch import HARNESS_EPOCH, SEASON_1_START_EPOCH


def test_constants():
    assert HARNESS_EPOCH >= SEASON_1_START_EPOCH
    assert SEASON_1_START_EPOCH >= 1
