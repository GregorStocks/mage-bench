"""Tests for harness epoch constants."""

from puppeteer.harness_epoch import HARNESS_EPOCH, MIN_LEADERBOARD_EPOCH


def test_constants():
    assert HARNESS_EPOCH >= MIN_LEADERBOARD_EPOCH
    assert MIN_LEADERBOARD_EPOCH >= 1
