"""Config-load tests for the [rate_limits] table (budget/window/anchor/epoch).

Covers duration parsing, the rolling/fixed anchor validation matrix, and the
load-time errors the user-approved schema calls for: `epoch` given with
`anchor = "rolling"`, and `anchor = "fixed"` given without `epoch`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import yas.config as config


def test_duration_parses_hours_and_days() -> None:
    assert config._parse_duration('5h') == 5 * 3600
    assert config._parse_duration('7d') == 7 * 86400


@pytest.mark.parametrize('bad', ['5', '5x', '-5h', '0h', 'h5', ''])
def test_duration_rejects_malformed_strings(bad: str) -> None:
    with pytest.raises(ValueError):
        config._parse_duration(bad)


def test_rate_limits_rolling_bucket_loads(tmp_path: Path) -> None:
    (tmp_path / 'yas.toml').write_text(
        '[rate_limits]\n'
        'five_hour = { budget = 44_000_000, window = "5h", anchor = "rolling" }\n'
    )
    cfg = config.Config.load(env={}, config_dir=tmp_path)
    assert not cfg.errors
    rule = cfg.rate_limit_rules['five_hour']
    assert rule.budget == 44_000_000
    assert rule.window_seconds == 5 * 3600
    assert rule.anchor == 'rolling'
    assert rule.epoch is None


def test_rate_limits_fixed_bucket_loads(tmp_path: Path) -> None:
    (tmp_path / 'yas.toml').write_text(
        '[rate_limits]\n'
        'seven_day = { budget = 440_000_000, window = "7d", anchor = "fixed", epoch = "0 0 * * 0" }\n'
    )
    cfg = config.Config.load(env={}, config_dir=tmp_path)
    assert not cfg.errors
    rule = cfg.rate_limit_rules['seven_day']
    assert rule.anchor == 'fixed'
    assert rule.epoch == '0 0 * * 0'


def test_absent_rate_limits_table_yields_no_rules(tmp_path: Path) -> None:
    cfg = config.Config.load(env={}, config_dir=tmp_path)
    assert cfg.rate_limit_rules == {}


def test_rolling_with_epoch_is_a_load_error(tmp_path: Path) -> None:
    (tmp_path / 'yas.toml').write_text(
        '[rate_limits]\n'
        'five_hour = { budget = 1000, window = "5h", anchor = "rolling", epoch = "0 0 * * 0" }\n'
    )
    cfg = config.Config.load(env={}, config_dir=tmp_path)
    assert 'rate_limits.five_hour' in cfg.errors
    assert 'five_hour' not in cfg.rate_limit_rules


def test_fixed_without_epoch_is_a_load_error(tmp_path: Path) -> None:
    (tmp_path / 'yas.toml').write_text(
        '[rate_limits]\n'
        'seven_day = { budget = 1000, window = "7d", anchor = "fixed" }\n'
    )
    cfg = config.Config.load(env={}, config_dir=tmp_path)
    assert 'rate_limits.seven_day' in cfg.errors
    assert 'seven_day' not in cfg.rate_limit_rules


def test_bad_cron_epoch_is_a_load_error(tmp_path: Path) -> None:
    (tmp_path / 'yas.toml').write_text(
        '[rate_limits]\n'
        'seven_day = { budget = 1000, window = "7d", anchor = "fixed", epoch = "@weekly" }\n'
    )
    cfg = config.Config.load(env={}, config_dir=tmp_path)
    assert 'rate_limits.seven_day' in cfg.errors
    assert 'seven_day' not in cfg.rate_limit_rules


def test_non_positive_budget_is_a_load_error(tmp_path: Path) -> None:
    (tmp_path / 'yas.toml').write_text(
        '[rate_limits]\n'
        'five_hour = { budget = 0, window = "5h", anchor = "rolling" }\n'
    )
    cfg = config.Config.load(env={}, config_dir=tmp_path)
    assert 'rate_limits.five_hour' in cfg.errors


def test_bad_anchor_is_a_load_error(tmp_path: Path) -> None:
    (tmp_path / 'yas.toml').write_text(
        '[rate_limits]\n'
        'five_hour = { budget = 1000, window = "5h", anchor = "sideways" }\n'
    )
    cfg = config.Config.load(env={}, config_dir=tmp_path)
    assert 'rate_limits.five_hour' in cfg.errors


def test_one_bad_bucket_does_not_block_the_other(tmp_path: Path) -> None:
    (tmp_path / 'yas.toml').write_text(
        '[rate_limits]\n'
        'five_hour = { budget = 44_000_000, window = "5h", anchor = "rolling" }\n'
        'seven_day = { budget = 440_000_000, window = "7d", anchor = "fixed" }\n'  # missing epoch
    )
    cfg = config.Config.load(env={}, config_dir=tmp_path)
    assert 'five_hour' in cfg.rate_limit_rules
    assert 'seven_day' not in cfg.rate_limit_rules
    assert 'rate_limits.seven_day' in cfg.errors
