"""Configuration is part of the audit trail, not just plumbing.

Every threshold a decision depends on is snapshotted onto the run row, so it
has to be immutable, serialisable and overridable from the environment --
otherwise a sweep silently runs every arm at the same settings.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from recon.config import MatchConfig, Settings


def test_match_config_is_frozen():
    config = MatchConfig()
    with pytest.raises(ValidationError):
        config.tier1_date_window_days = 30  # type: ignore[misc]


def test_confidences_are_decimal_not_float():
    """Confidence reaches a numeric(4,3) column; a float would round on the way."""
    config = MatchConfig()
    for value in (
        config.tier0_confidence,
        config.tier1_confidence,
        config.tier1_fx_confidence,
        config.tier3_autocommit_confidence,
    ):
        assert isinstance(value, Decimal)


def test_config_snapshot_round_trips_as_json():
    """It is stored as jsonb on the run row; a type that will not serialise
    there makes the run unreplayable."""
    config = MatchConfig()
    restored = MatchConfig(**json.loads(config.model_dump_json()))
    assert restored == config


def test_thresholds_are_overridable_from_the_environment(monkeypatch):
    monkeypatch.setenv("MATCH__TIER1_DATE_WINDOW_DAYS", "0")
    monkeypatch.setenv("MATCH__TIER1_FX_TOLERANCE_BPS", "25")
    match = Settings().match
    assert match.tier1_date_window_days == 0
    assert match.tier1_fx_tolerance_bps == 25


def test_defaults_hold_the_briefs_thresholds():
    config = MatchConfig()
    assert config.tier0_date_window_days == 2
    assert config.tier1_date_window_days == 7
    assert config.tier1_confidence == Decimal("0.950")
    assert config.tier3_autocommit_confidence == Decimal("0.900")
    assert config.candidate_limit == 10
