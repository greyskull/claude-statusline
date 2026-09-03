"""Regression tests for RateLimitLog.record's append-only write path and its
lock-guarded compaction (yas.tokens) -- see defects 2/3 in the rate-limit
window-aggregation diagnosis: the old read-all -> write_text(whole file)
path destroyed legacy 3-field lines and lost samples under concurrency."""
from __future__ import annotations

import threading
from pathlib import Path

from yas.constants import rate_limit_log
from yas.tokens import RateLimitLog

KEEP = 6 * 3600


def _raw_lines(tmp_home: Path) -> list[str]:
    log = rate_limit_log()
    if not log.exists():
        return []
    return [ln for ln in log.read_text().splitlines() if ln]


def test_legacy_3_field_lines_survive_a_record_call(tmp_home: Path) -> None:
    """Legacy 3-field lines (`ts session_id cumulative_tokens`) are a
    different, incompatible signal that `_parse` skips on read -- but
    `record()` must never destroy them; they age out via retention only."""
    log = rate_limit_log()
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        '100.000 legacy-a 12345\n'
        '110.000 sess-a 100 20 5 50\n'
        '120.000 legacy-b 999\n'
    )

    RateLimitLog.record('sess-a', 200, 20, 5, 50, keep_seconds=KEEP, now=200.0)

    lines = _raw_lines(tmp_home)
    assert '100.000 legacy-a 12345' in lines
    assert '120.000 legacy-b 999' in lines


def test_concurrent_writers_lose_no_sessions_final_sample(tmp_home: Path) -> None:
    """N threads each recording M distinct sessions' single sample must all
    land -- the append-only path (no whole-file rewrite) makes concurrent
    writers safe. (The old read-modify-write path lost 590/600 samples
    under load.)"""
    writers = 6
    sessions_per_writer = 15

    def _write(writer_idx: int) -> None:
        for i in range(sessions_per_writer):
            sid = f'w{writer_idx}-s{i}'
            RateLimitLog.record(sid, 100 + i, 0, 0, 0, keep_seconds=KEEP, now=float(1000 + i))

    threads = [threading.Thread(target=_write, args=(w,)) for w in range(writers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    by_session = RateLimitLog._parse()
    expected_ids = {f'w{w}-s{i}' for w in range(writers) for i in range(sessions_per_writer)}
    assert set(by_session) == expected_ids
    for w in range(writers):
        for i in range(sessions_per_writer):
            sid = f'w{w}-s{i}'
            assert by_session[sid][-1][1:] == (100 + i, 0, 0, 0)


def test_compaction_still_prunes_rows_older_than_keep_seconds(tmp_home: Path) -> None:
    RateLimitLog.record('sess-a', 100, 0, 0, 0, keep_seconds=100, now=0.0)
    # Well past the 100s retention window -- the write must trigger compaction.
    RateLimitLog.record('sess-a', 200, 0, 0, 0, keep_seconds=100, now=500.0)
    assert _raw_lines(tmp_home) == ['500.000 sess-a 200 0 0 0']


def test_append_path_does_not_rewrite_the_file(tmp_home: Path) -> None:
    """An unrelated trailing line (e.g. one written by another process
    between calls) must survive a `record()` that only appends -- proving
    the write path is `open(..., 'a')`, not a whole-file rewrite."""
    log = rate_limit_log()
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text('110.000 sess-a 100 20 5 50\n')

    with open(log, 'a') as f:
        f.write('115.000 sentinel-session 1 1 1 1\n')

    RateLimitLog.record('sess-a', 200, 20, 5, 50, keep_seconds=KEEP, now=200.0)

    lines = _raw_lines(tmp_home)
    assert '115.000 sentinel-session 1 1 1 1' in lines
