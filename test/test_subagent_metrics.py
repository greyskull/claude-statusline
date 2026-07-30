from yas.render.metrics import subagent_avg_tpm


class TestSubagentAvgTpm:
    def test_normal_case(self) -> None:
        # 300 input + 300 output = 600 tokens over 120 seconds (2 min) = 300 t/m
        result = subagent_avg_tpm(
            total_input=300,
            output=300,
            first_timestamp=1_000_000.0,
            now=1_000_120.0,
            floor_seconds=3.0,
        )
        assert result == 300

    def test_returns_none_when_first_timestamp_zero(self) -> None:
        result = subagent_avg_tpm(
            total_input=1000,
            output=500,
            first_timestamp=0,
            now=60.0,
        )
        assert result is None

    def test_returns_none_when_elapsed_below_floor(self) -> None:
        # elapsed = 2.0s < floor_seconds = 3.0s
        result = subagent_avg_tpm(
            total_input=1000,
            output=500,
            first_timestamp=10.0,
            now=12.0,
        )
        assert result is None

    def test_returns_none_when_elapsed_just_below_floor(self) -> None:
        # elapsed = 2.99s < floor_seconds = 3.0s → None
        result = subagent_avg_tpm(
            total_input=1000,
            output=500,
            first_timestamp=10.0,
            now=12.99,
        )
        assert result is None

    def test_returns_value_just_above_floor(self) -> None:
        # elapsed = 3.01s > 3.0s floor → should return a value
        result = subagent_avg_tpm(
            total_input=1000,
            output=500,
            first_timestamp=10.0,
            now=13.01,
        )
        assert result is not None


# `subagent_share` and its `TestSubagentShare` coverage were removed along
# with the `(N.N%)` session-share suffix on the subagent row's token field —
# see CONTEXT.md "Session Share %" for the historical record of the term.
