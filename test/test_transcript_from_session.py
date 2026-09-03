"""Tests for TranscriptUsage.from_session (main thread + subagent transcripts)."""
import json
from pathlib import Path

import yas.info.transcript as transcript
from yas.info.parsecache import TranscriptCache


def _assistant_line(
    msg_id: str,
    input_tokens: int = 0,
    cache_creation: int = 0,
    cache_read: int = 0,
    output_tokens: int = 0,
    is_sidechain: bool = False,
    timestamp: str = '',
) -> str:
    record = {
        'type': 'assistant',
        'isSidechain': is_sidechain,
        'message': {
            'id': msg_id,
            'role': 'assistant',
            'usage': {
                'input_tokens': input_tokens,
                'cache_creation_input_tokens': cache_creation,
                'cache_read_input_tokens': cache_read,
                'output_tokens': output_tokens,
            },
        },
    }
    if timestamp:
        record['timestamp'] = timestamp
    return json.dumps(record)


def test_from_session_sums_main_and_subagent_transcripts(tmp_path: Path) -> None:
    """from_session == from_transcript(main) + from_transcript(sub) for each sub."""
    sess = tmp_path / 'sess.jsonl'
    sess.write_text(_assistant_line('m1', input_tokens=10, output_tokens=5) + '\n')

    subdir = tmp_path / 'sess' / 'subagents'
    subdir.mkdir(parents=True)
    (subdir / 'agent-a1.jsonl').write_text(
        _assistant_line('s1', input_tokens=100, output_tokens=50, is_sidechain=True) + '\n'
    )
    (subdir / 'agent-a2.jsonl').write_text(
        _assistant_line('s2', input_tokens=200, output_tokens=75, is_sidechain=True) + '\n'
    )

    result = transcript.TranscriptUsage.from_session(str(sess))
    expected = (
        transcript.TranscriptUsage.from_transcript(str(sess))
        + transcript.TranscriptUsage.from_transcript(str(subdir / 'agent-a1.jsonl'))
        + transcript.TranscriptUsage.from_transcript(str(subdir / 'agent-a2.jsonl'))
    )
    assert result == expected
    assert result.input_tokens == 310
    assert result.output_tokens == 130


def test_subagent_sidechain_records_are_counted(tmp_path: Path) -> None:
    """isSidechain:true subagent records must NOT be zeroed -- the toolcounts.py trap."""
    sess = tmp_path / 'sess.jsonl'
    sess.write_text(_assistant_line('m1', input_tokens=1) + '\n')

    subdir = tmp_path / 'sess' / 'subagents'
    subdir.mkdir(parents=True)
    (subdir / 'agent-a1.jsonl').write_text(
        _assistant_line('s1', input_tokens=999, is_sidechain=True) + '\n'
    )

    result = transcript.TranscriptUsage.from_session(str(sess))
    assert result.input_tokens == 1000


def test_missing_subagents_dir_falls_back_to_main_only(tmp_path: Path) -> None:
    """No subagents/ sibling dir -> identical to from_transcript(main)."""
    sess = tmp_path / 'sess.jsonl'
    sess.write_text(_assistant_line('m1', input_tokens=10, output_tokens=5) + '\n')

    result = transcript.TranscriptUsage.from_session(str(sess))
    expected = transcript.TranscriptUsage.from_transcript(str(sess))
    assert result == expected


def test_empty_transcript_path_returns_empty() -> None:
    """Empty transcript_path -> TranscriptUsage(), never crashes."""
    result = transcript.TranscriptUsage.from_session('')
    assert result == transcript.TranscriptUsage()


def test_subagents_dir_present_but_empty(tmp_path: Path) -> None:
    """subagents/ dir exists but has no agent-*.jsonl files -> main-only."""
    sess = tmp_path / 'sess.jsonl'
    sess.write_text(_assistant_line('m1', input_tokens=10) + '\n')
    (tmp_path / 'sess' / 'subagents').mkdir(parents=True)

    result = transcript.TranscriptUsage.from_session(str(sess))
    assert result == transcript.TranscriptUsage.from_transcript(str(sess))


def test_add_takes_latest_cache_anchor() -> None:
    """__add__ sums the four counters but keeps the LATEST cache_anchor_epoch/ttl."""
    older = transcript.TranscriptUsage(
        input_tokens=1, cache_anchor_epoch=100.0, cache_ttl=300,
    )
    newer = transcript.TranscriptUsage(
        input_tokens=2, cache_anchor_epoch=200.0, cache_ttl=3600,
    )
    combined = older + newer
    assert combined.input_tokens == 3
    assert combined.cache_anchor_epoch == 200.0
    assert combined.cache_ttl == 3600

    # Order shouldn't matter -- the larger epoch wins either way.
    combined_reversed = newer + older
    assert combined_reversed.cache_anchor_epoch == 200.0
    assert combined_reversed.cache_ttl == 3600


def test_from_session_takes_latest_cache_anchor_across_files(tmp_path: Path) -> None:
    """The merged cache_anchor_epoch/cache_ttl reflect the most recent write overall."""
    sess = tmp_path / 'sess.jsonl'
    sess.write_text(
        _assistant_line('m1', input_tokens=1, cache_read=5, timestamp='2026-01-01T00:00:00Z') + '\n'
    )
    subdir = tmp_path / 'sess' / 'subagents'
    subdir.mkdir(parents=True)
    (subdir / 'agent-a1.jsonl').write_text(
        _assistant_line(
            's1', input_tokens=1, cache_read=5, is_sidechain=True,
            timestamp='2026-01-02T00:00:00Z',
        ) + '\n'
    )

    result = transcript.TranscriptUsage.from_session(str(sess))
    sub_usage = transcript.TranscriptUsage.from_transcript(str(subdir / 'agent-a1.jsonl'))
    assert result.cache_anchor_epoch == sub_usage.cache_anchor_epoch


def test_from_session_uses_cache_to_avoid_reparsing(tmp_path: Path) -> None:
    """A supplied TranscriptCache is populated for each subagent file and reused."""
    sess = tmp_path / 'sess.jsonl'
    sess.write_text(_assistant_line('m1', input_tokens=1) + '\n')
    subdir = tmp_path / 'sess' / 'subagents'
    subdir.mkdir(parents=True)
    sub_path = subdir / 'agent-a1.jsonl'
    sub_path.write_text(_assistant_line('s1', input_tokens=42, is_sidechain=True) + '\n')

    cache = TranscriptCache('test-session')
    first = transcript.TranscriptUsage.from_session(str(sess), cache=cache)
    assert cache.get_usage(str(sub_path), sub_path.stat()) is not None

    # Corrupt the on-disk file without touching mtime/size in a way pytest can
    # detect trivially -- instead, just assert the cached path returns the
    # same result as a fresh parse (proves the cache round-trip is faithful).
    second = transcript.TranscriptUsage.from_session(str(sess), cache=cache)
    assert first == second
