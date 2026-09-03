"""Config-load tests for the [rate_limits.weights] table.

Covers: defaults when absent (byte-identical to today), per-key override,
partial tables, the table being valid-but-inert with no bucket rules, that
`weights` isn't mistaken for a bucket name by [rate_limits] parsing, and
load-time rejection of non-numeric/negative values.
"""

from __future__ import annotations

from pathlib import Path

import yas.config as config
from yas.config import RateLimitWeights


def test_absent_table_yields_pricing_derived_defaults(tmp_path: Path) -> None:
    cfg = config.Config.load(env={}, config_dir=tmp_path)
    assert not cfg.errors
    assert cfg.rate_limit_weights == RateLimitWeights(
        input=1.0, cache_creation=1.25, cache_read=0.1, output=5.0)


def test_empty_table_yields_the_same_defaults(tmp_path: Path) -> None:
    (tmp_path / 'yas.toml').write_text('[rate_limits.weights]\n')
    cfg = config.Config.load(env={}, config_dir=tmp_path)
    assert not cfg.errors
    assert cfg.rate_limit_weights == RateLimitWeights(
        input=1.0, cache_creation=1.25, cache_read=0.1, output=5.0)


def test_each_key_can_be_overridden_individually() -> None:
    for key, value in (('input', 2.0), ('cache_creation', 3.0), ('cache_read', 0.5), ('output', 4.0)):
        errors: list[str] = []
        debug: list[str] = []
        weights = config._parse_rate_limit_weights({key: value}, errors, debug)
        assert not errors
        assert getattr(weights, key) == value
        for other in ('input', 'cache_creation', 'cache_read', 'output'):
            if other != key:
                assert getattr(weights, other) == getattr(config.DEFAULT_RATE_LIMIT_WEIGHTS, other)


def test_partial_table_keeps_defaults_for_omitted_keys(tmp_path: Path) -> None:
    (tmp_path / 'yas.toml').write_text(
        '[rate_limits.weights]\n'
        'cache_read = 0.025\n'  # e.g. Fable 5.1 / Mythos 5.1 pricing
    )
    cfg = config.Config.load(env={}, config_dir=tmp_path)
    assert not cfg.errors
    assert cfg.rate_limit_weights.cache_read == 0.025
    assert cfg.rate_limit_weights.input == 1.0
    assert cfg.rate_limit_weights.cache_creation == 1.25
    assert cfg.rate_limit_weights.output == 5.0


def test_int_values_are_coerced_to_float(tmp_path: Path) -> None:
    (tmp_path / 'yas.toml').write_text(
        '[rate_limits.weights]\n'
        'output = 5\n'
    )
    cfg = config.Config.load(env={}, config_dir=tmp_path)
    assert not cfg.errors
    assert cfg.rate_limit_weights.output == 5.0
    assert isinstance(cfg.rate_limit_weights.output, float)


def test_zero_is_a_valid_weight(tmp_path: Path) -> None:
    (tmp_path / 'yas.toml').write_text(
        '[rate_limits.weights]\n'
        'cache_read = 0\n'
    )
    cfg = config.Config.load(env={}, config_dir=tmp_path)
    assert not cfg.errors
    assert cfg.rate_limit_weights.cache_read == 0.0


def test_weights_table_with_no_bucket_rules_is_valid_but_inert(tmp_path: Path) -> None:
    (tmp_path / 'yas.toml').write_text(
        '[rate_limits.weights]\n'
        'output = 2.0\n'
    )
    cfg = config.Config.load(env={}, config_dir=tmp_path)
    assert not cfg.errors
    assert cfg.rate_limit_rules == {}          # no five_hour/seven_day -> simulator stays off
    assert cfg.rate_limit_weights.output == 2.0  # but the weight is still parsed


def test_weights_key_is_not_mistaken_for_a_bucket_rule(tmp_path: Path) -> None:
    (tmp_path / 'yas.toml').write_text(
        '[rate_limits]\n'
        'five_hour = { budget = 1000, window = "5h", anchor = "rolling" }\n'
        '[rate_limits.weights]\n'
        'output = 2.0\n'
    )
    cfg = config.Config.load(env={}, config_dir=tmp_path)
    assert not cfg.errors
    assert set(cfg.rate_limit_rules) == {'five_hour'}
    assert 'weights' not in cfg.rate_limit_rules
    assert cfg.rate_limit_weights.output == 2.0


def test_non_numeric_value_is_a_load_error_and_falls_back_to_default(tmp_path: Path) -> None:
    (tmp_path / 'yas.toml').write_text(
        '[rate_limits.weights]\n'
        'input = "a lot"\n'
    )
    cfg = config.Config.load(env={}, config_dir=tmp_path)
    assert 'rate_limits.weights.input' in cfg.errors
    assert cfg.rate_limit_weights.input == 1.0


def test_negative_value_is_a_load_error_and_falls_back_to_default(tmp_path: Path) -> None:
    (tmp_path / 'yas.toml').write_text(
        '[rate_limits.weights]\n'
        'output = -1.0\n'
    )
    cfg = config.Config.load(env={}, config_dir=tmp_path)
    assert 'rate_limits.weights.output' in cfg.errors
    assert cfg.rate_limit_weights.output == 5.0


def test_bool_value_is_rejected_not_silently_coerced(tmp_path: Path) -> None:
    (tmp_path / 'yas.toml').write_text(
        '[rate_limits.weights]\n'
        'cache_read = true\n'
    )
    cfg = config.Config.load(env={}, config_dir=tmp_path)
    assert 'rate_limits.weights.cache_read' in cfg.errors
    assert cfg.rate_limit_weights.cache_read == 0.1


def test_one_bad_key_does_not_block_the_others(tmp_path: Path) -> None:
    (tmp_path / 'yas.toml').write_text(
        '[rate_limits.weights]\n'
        'input = -1.0\n'
        'output = 3.0\n'
    )
    cfg = config.Config.load(env={}, config_dir=tmp_path)
    assert 'rate_limits.weights.input' in cfg.errors
    assert cfg.rate_limit_weights.input == 1.0   # rejected -> default
    assert cfg.rate_limit_weights.output == 3.0  # valid -> honoured
