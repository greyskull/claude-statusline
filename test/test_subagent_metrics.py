from yas.render.metrics import subagent_avg_tpm, subagent_dur_str


class _FakeSub:
    """Minimal stand-in for RunningSubagent — subagent_dur_str only reads
    status/end_ts/first_timestamp/run_start_ts (via getattr/attr access), so
    a plain object avoids dragging in the full RunningSubagent constructor
    for pure duration-math tests."""

    def __init__(
        self,
        status: str = 'running',
        end_ts: float = 0.0,
        first_timestamp: float = 0.0,
        run_start_ts: float = 0.0,
    ) -> None:
        self.status          = status
        self.end_ts          = end_ts
        self.first_timestamp = first_timestamp
        self.run_start_ts    = run_start_ts


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


class TestSubagentDurStr:
    """Regression coverage for the resumed-agent elapsed-time fix: a resumed
    agent's displayed duration must measure from the CURRENT run's start
    (run_start_ts), not the original spawn (first_timestamp), in either
    branch of subagent_dur_str. Timestamps are anchored off an arbitrary
    fixed epoch, never the real wall clock, so the tests never go stale."""

    EPOCH = 2_000_000_000.0  # arbitrary fixed anchor, not wall-clock derived

    def test_resumed_running_measures_from_resume_boundary(self) -> None:
        # Mirrors the real repro: original spawn ~65 min ago, resume (and thus
        # run_start_ts) ~10 min 43s ago. The old first_timestamp-only logic
        # would have shown ~1:05:xx; the fix must show ~10:43.
        now = self.EPOCH
        sub = _FakeSub(
            status='running',
            first_timestamp=now - 65 * 60,
            run_start_ts=now - (10 * 60 + 43),
        )
        assert subagent_dur_str(sub, now) == '10:43'

    def test_never_resumed_running_unchanged_from_first_timestamp(self) -> None:
        # run_start_ts == first_timestamp (never resumed): behaviour is
        # unchanged from before the fix.
        now = self.EPOCH
        sub = _FakeSub(status='running', first_timestamp=now - 90, run_start_ts=now - 90)
        assert subagent_dur_str(sub, now) == ' 1:30'

    def test_terminal_resumed_never_collapses_to_zero(self) -> None:
        # Regression guard for the terminal-resumed bracket bug: run_start_ts
        # sitting only milliseconds before end_ts (the pre-fix symptom, where
        # run_start_ts was wrongly anchored on the SAME notification as
        # end_ts) must not render as ~0:00 for a run that actually took
        # minutes. This asserts the healthy-input contract at the dur_str
        # layer; from_session's own bracket-selection is covered separately
        # in test_subagent_notifications.py.
        now = self.EPOCH
        sub = _FakeSub(
            status='completed',
            first_timestamp=now - 3600,
            run_start_ts=now - 210,  # correct bracket: run started 3:30 before it ended
            end_ts=now,
        )
        result = subagent_dur_str(sub, now)
        assert result == ' 3:30'
        assert result != ' 0:00'

    def test_resumed_terminal_measures_end_ts_minus_run_start(self) -> None:
        # Resumed-then-finished: the terminal branch must also anchor on
        # run_start_ts, not first_timestamp — the bug affected both branches.
        now = self.EPOCH
        sub = _FakeSub(
            status='completed',
            first_timestamp=now - 65 * 60,
            run_start_ts=now - 700,
            end_ts=now - 60,  # finished 60s ago; run started 700s ago -> 10:40
        )
        assert subagent_dur_str(sub, now) == '10:40'

    def test_run_start_ts_equals_first_timestamp_when_no_notification(self) -> None:
        # RunningSubagent's own default (run_start_ts unset -> first_timestamp)
        # is exercised end-to-end here via the real class, not _FakeSub.
        from yas.info.subagents import RunningSubagent

        sub = RunningSubagent(
            agent_type='general-purpose', description='', billed_in=0, output=0,
            first_timestamp=1_000_000.0,
        )
        assert sub.run_start_ts == sub.first_timestamp == 1_000_000.0
