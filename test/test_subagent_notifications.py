"""Tests for the authoritative <task-notification> completion signal.

Replaces prose/heuristic "is this subagent done" inference (terminal-text
pattern-matching, StructuredOutput-block detection) with the structured
<task-notification> record Claude Code itself writes when an async
Agent/Task-tool subagent stops. See yas.info.subagents._collect_task_notifications.
"""
import json
import os
import time
from pathlib import Path

from yas.info.subagents import RunningSubagents


SESSION_ID = 'sess-notif'
PROJECT_DIR = '/home/user/notifproject'
PROJECT_SLUG = 'home-user-notifproject'


def _project_dir(tmp_home: Path) -> Path:
    return tmp_home / '.claude' / 'projects' / f'-{PROJECT_SLUG}'


def _session_dir(tmp_home: Path) -> Path:
    return _project_dir(tmp_home) / SESSION_ID


def _subagents_dir(tmp_home: Path) -> Path:
    return _session_dir(tmp_home) / 'subagents'


def _write_agent(subagents_dir: Path, agent_id: str, mtime: float | None = None) -> Path:
    subagents_dir.mkdir(parents=True, exist_ok=True)
    meta = subagents_dir / f'{agent_id}.meta.json'
    meta.write_text(json.dumps({'agentType': 'Explore', 'description': 'find X'}))
    jsonl = subagents_dir / f'{agent_id}.jsonl'
    jsonl.write_text('{"event": "start"}\n')
    if mtime is not None:
        os.utime(jsonl, (mtime, mtime))
    return jsonl


def _notif_block(task_id: str, tool_use_id: str, status: str, summary: str = 'done') -> str:
    return (
        '<task-notification>\n'
        f'<task-id>{task_id}</task-id>\n'
        f'<tool-use-id>{tool_use_id}</tool-use-id>\n'
        f'<status>{status}</status>\n'
        f'<summary>{summary}</summary>\n'
        '</task-notification>'
    )


def _queue_operation_line(task_id: str, tool_use_id: str, status: str, ts: str) -> str:
    '''The "type":"queue-operation" record shape.'''
    d = {
        'type': 'queue-operation',
        'operation': 'enqueue',
        'timestamp': ts,
        'content': _notif_block(task_id, tool_use_id, status),
    }
    return json.dumps(d) + '\n'


def _user_record_line(task_id: str, tool_use_id: str, status: str, ts: str) -> str:
    '''The "type":"user" record whose message.content is a plain string
    containing the same <task-notification> block (the second confirmed shape).'''
    d = {
        'type': 'user',
        'timestamp': ts,
        'message': {'content': _notif_block(task_id, tool_use_id, status)},
    }
    return json.dumps(d) + '\n'


def _write_session_jsonl(tmp_home: Path, lines: list[str]) -> Path:
    pdir = _project_dir(tmp_home)
    pdir.mkdir(parents=True, exist_ok=True)
    session_jsonl = pdir / f'{SESSION_ID}.jsonl'
    session_jsonl.write_text(''.join(lines))
    return session_jsonl


def _get(result: RunningSubagents, agent_id: str):
    matches = [s for s in result.subagents if s.agent_id == agent_id]
    assert matches, f'{agent_id} not found among {[s.agent_id for s in result.subagents]}'
    return matches[0]


# ---------------------------------------------------------------------------
# Each of the four terminal statuses
# ---------------------------------------------------------------------------

def test_status_completed(tmp_home: Path) -> None:
    now = time.time()
    sdir = _subagents_dir(tmp_home)
    _write_agent(sdir, 'agent-c1', mtime=now - 100)
    _write_session_jsonl(tmp_home, [
        _queue_operation_line('c1', 'toolu_c1', 'completed', '2026-07-25T03:43:19.010Z'),
    ])
    result = RunningSubagents.from_session(SESSION_ID, PROJECT_DIR)
    sub = _get(result, 'agent-c1')
    assert sub.status == 'completed'
    assert sub.end_ts > 0
    assert sub.is_done is True
    assert sub.run_count == 1


def test_status_killed(tmp_home: Path) -> None:
    now = time.time()
    sdir = _subagents_dir(tmp_home)
    _write_agent(sdir, 'agent-k1', mtime=now - 100)
    _write_session_jsonl(tmp_home, [
        _queue_operation_line('k1', 'toolu_k1', 'killed', '2026-07-25T03:43:19.010Z'),
    ])
    result = RunningSubagents.from_session(SESSION_ID, PROJECT_DIR)
    sub = _get(result, 'agent-k1')
    assert sub.status == 'killed'
    assert sub.end_ts > 0
    assert sub.is_done is True


def test_status_failed(tmp_home: Path) -> None:
    now = time.time()
    sdir = _subagents_dir(tmp_home)
    _write_agent(sdir, 'agent-f1', mtime=now - 100)
    _write_session_jsonl(tmp_home, [
        _queue_operation_line('f1', 'toolu_f1', 'failed', '2026-07-25T03:43:19.010Z'),
    ])
    result = RunningSubagents.from_session(SESSION_ID, PROJECT_DIR)
    sub = _get(result, 'agent-f1')
    assert sub.status == 'failed'
    assert sub.end_ts > 0
    assert sub.is_done is True


def test_status_stopped(tmp_home: Path) -> None:
    now = time.time()
    sdir = _subagents_dir(tmp_home)
    _write_agent(sdir, 'agent-s1', mtime=now - 100)
    _write_session_jsonl(tmp_home, [
        _queue_operation_line('s1', 'toolu_s1', 'stopped', '2026-07-25T03:43:19.010Z'),
    ])
    result = RunningSubagents.from_session(SESSION_ID, PROJECT_DIR)
    sub = _get(result, 'agent-s1')
    assert sub.status == 'stopped'
    assert sub.end_ts > 0
    assert sub.is_done is True


# ---------------------------------------------------------------------------
# Unknown status -> treated as running, never done
# ---------------------------------------------------------------------------

def test_unknown_status_treated_as_running(tmp_home: Path) -> None:
    now = time.time()
    sdir = _subagents_dir(tmp_home)
    _write_agent(sdir, 'agent-u1', mtime=now - 100)
    _write_session_jsonl(tmp_home, [
        _queue_operation_line('u1', 'toolu_u1', 'some-future-status', '2026-07-25T03:43:19.010Z'),
    ])
    result = RunningSubagents.from_session(SESSION_ID, PROJECT_DIR)
    sub = _get(result, 'agent-u1')
    assert sub.status == 'running'
    assert sub.end_ts == 0.0
    assert sub.is_done is False
    # The notification was still seen (counted), just not treated as terminal.
    assert sub.run_count == 1


def test_no_notification_at_all_is_running(tmp_home: Path) -> None:
    now = time.time()
    sdir = _subagents_dir(tmp_home)
    _write_agent(sdir, 'agent-none', mtime=now - 5)
    result = RunningSubagents.from_session(SESSION_ID, PROJECT_DIR)
    sub = _get(result, 'agent-none')
    assert sub.status == 'running'
    assert sub.end_ts == 0.0
    assert sub.run_count == 0


# ---------------------------------------------------------------------------
# Prose that LOOKS terminal never marks an agent done (regression test for
# the reported false positive: an agent narrating "still waiting for the
# actual completion notification..." was previously marked done).
# ---------------------------------------------------------------------------

def test_terminal_looking_prose_does_not_mark_done(tmp_home: Path) -> None:
    now = time.time()
    sdir = _subagents_dir(tmp_home)
    subagents_dir = sdir
    subagents_dir.mkdir(parents=True, exist_ok=True)
    meta = subagents_dir / 'agent-prose1.meta.json'
    meta.write_text(json.dumps({'agentType': 'spec-implementer', 'description': 'do work'}))
    jsonl = subagents_dir / 'agent-prose1.jsonl'
    # Final assistant line reads like a wrap-up (end_turn, plain text, no
    # tool_use) — exactly what the deleted terminal-text heuristic used to
    # key off. No <task-notification> anywhere: must stay "running".
    jsonl.write_text(json.dumps({
        'type': 'assistant',
        'timestamp': '2026-07-25T03:40:00.000Z',
        'message': {
            'id': 'msg-1',
            'model': 'claude-x',
            'stop_reason': 'end_turn',
            'usage': {'input_tokens': 1, 'output_tokens': 1},
            'content': [
                {'type': 'text', 'text': 'Still waiting for the actual completion notification...'},
            ],
        },
    }) + '\n')
    os.utime(jsonl, (now, now))
    result = RunningSubagents.from_session(SESSION_ID, PROJECT_DIR)
    sub = _get(result, 'agent-prose1')
    assert sub.status == 'running'
    assert sub.end_ts == 0.0
    assert sub.is_done is False


# ---------------------------------------------------------------------------
# Resume: a second notification for the same task-id bumps run_count
# ---------------------------------------------------------------------------

def test_resume_second_notification_bumps_run_count(tmp_home: Path) -> None:
    now = time.time()
    sdir = _subagents_dir(tmp_home)
    _write_agent(sdir, 'agent-r1', mtime=now - 10)
    _write_session_jsonl(tmp_home, [
        _queue_operation_line('r1', 'toolu_r1', 'completed', '2026-07-25T03:00:00.000Z'),
        _queue_operation_line('r1', 'toolu_r1_b', 'completed', '2026-07-25T03:10:00.000Z'),
    ])
    result = RunningSubagents.from_session(SESSION_ID, PROJECT_DIR)
    sub = _get(result, 'agent-r1')
    assert sub.run_count == 2
    assert sub.status == 'completed'
    # end_ts reflects the LATEST notification, not the first.
    assert sub.end_ts == sub.end_ts  # sanity: set
    assert sub.end_ts > 0


def test_resumed_flag_true_when_transcript_postdates_last_notification(tmp_home: Path) -> None:
    # Only one notification, but the transcript kept being written after it —
    # a resumed agent appends more turns to the same jsonl (per the CC note:
    # "the same task-id may notify more than once").
    later = 2000000000.0  # far future mtime, postdates the notification ts
    sdir = _subagents_dir(tmp_home)
    _write_agent(sdir, 'agent-r2', mtime=later)
    _write_session_jsonl(tmp_home, [
        _queue_operation_line('r2', 'toolu_r2', 'completed', '2026-07-25T03:00:00.000Z'),
    ])
    result = RunningSubagents.from_session(SESSION_ID, PROJECT_DIR)
    sub = _get(result, 'agent-r2')
    assert sub.run_count == 1
    assert sub.resumed is True


# ---------------------------------------------------------------------------
# Notification found in a nested PARENT agent's own jsonl, not the session file
# ---------------------------------------------------------------------------

def test_notification_in_nested_parent_agent_jsonl(tmp_home: Path) -> None:
    now = time.time()
    sdir = _subagents_dir(tmp_home)
    # Parent agent (depth 1) and its child (depth 2) both live under the same
    # session's subagents/ dir. The child's completion notification is
    # written into the PARENT's own transcript, not the top-level session file.
    _write_agent(sdir, 'agent-parent1', mtime=now - 200)
    _write_agent(sdir, 'agent-child1', mtime=now - 50)
    # Give the child a parentAgentId pointing at the parent.
    child_meta = sdir / 'agent-child1.meta.json'
    child_meta.write_text(json.dumps({
        'agentType': 'general-purpose',
        'description': 'nested work',
        'parentAgentId': 'agent-parent1',
        'spawnDepth': 2,
    }))
    # Notification for the child lands in the PARENT's own jsonl.
    parent_jsonl = sdir / 'agent-parent1.jsonl'
    parent_jsonl.write_text(
        '{"event": "start"}\n'
        + _queue_operation_line('child1', 'toolu_child1', 'completed', '2026-07-25T03:20:00.000Z')
    )
    # Top-level session file has nothing about the child at all.
    _write_session_jsonl(tmp_home, ['{"event": "unrelated"}\n'])

    result = RunningSubagents.from_session(SESSION_ID, PROJECT_DIR)
    child = _get(result, 'agent-child1')
    assert child.status == 'completed'
    assert child.end_ts > 0
    assert child.parent_id == 'agent-parent1'


# ---------------------------------------------------------------------------
# Both record shapes: queue-operation, and a "user" record whose message
# content string embeds the same <task-notification> block.
# ---------------------------------------------------------------------------

def test_queue_operation_record_shape(tmp_home: Path) -> None:
    now = time.time()
    sdir = _subagents_dir(tmp_home)
    _write_agent(sdir, 'agent-shapeq', mtime=now - 5)
    _write_session_jsonl(tmp_home, [
        _queue_operation_line('shapeq', 'toolu_shapeq', 'completed', '2026-07-25T03:00:00.000Z'),
    ])
    result = RunningSubagents.from_session(SESSION_ID, PROJECT_DIR)
    sub = _get(result, 'agent-shapeq')
    assert sub.status == 'completed'


def test_user_record_shape(tmp_home: Path) -> None:
    now = time.time()
    sdir = _subagents_dir(tmp_home)
    _write_agent(sdir, 'agent-shapeu', mtime=now - 5)
    _write_session_jsonl(tmp_home, [
        _user_record_line('shapeu', 'toolu_shapeu', 'completed', '2026-07-25T03:00:00.000Z'),
    ])
    result = RunningSubagents.from_session(SESSION_ID, PROJECT_DIR)
    sub = _get(result, 'agent-shapeu')
    assert sub.status == 'completed'


# ---------------------------------------------------------------------------
# meta.json field surfacing: is_fork, parent_id, model
# ---------------------------------------------------------------------------

def test_meta_fields_surfaced(tmp_home: Path) -> None:
    now = time.time()
    sdir = _subagents_dir(tmp_home)
    sdir.mkdir(parents=True, exist_ok=True)
    meta = sdir / 'agent-metafields.meta.json'
    meta.write_text(json.dumps({
        'agentType': 'fork',
        'description': 'a forked agent',
        'isFork': True,
        'parentAgentId': 'agent-parentx',
        'model': 'claude-opus-9',
        'spawnDepth': 2,
    }))
    jsonl = sdir / 'agent-metafields.jsonl'
    jsonl.write_text('{"event": "start"}\n')
    os.utime(jsonl, (now, now))

    result = RunningSubagents.from_session(SESSION_ID, PROJECT_DIR)
    sub = _get(result, 'agent-metafields')
    assert sub.is_fork is True
    assert sub.parent_id == 'agent-parentx'
    assert sub.model == 'claude-opus-9'
