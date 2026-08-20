"""Tests for cache equivalence — task 6.4.

The headline guarantee: a SessionView render with a warm cache produces identical
RunningSubagents, ToolCounts, and session_inout fields as a cold render, proving
the cache doesn't alter semantics. Covers cold vs warm, and partially-warm variants.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from test_running_subagents import (
    _write_agent,
    _assistant_line,
)
from yas.info import SessionView
from yas.info.parsecache import TranscriptCache, cache_path
from yas.info.subagents import _notif_tail_cache, _tool_result_tail_cache
from yas.session import SessionInfo
from yas.config import Config


SESSION_FILE = Path(__file__).parent.parent / 'ops' / 'session-info-example.json'


def _session():
    """Load the example SessionInfo."""
    return SessionInfo.from_dict(json.loads(SESSION_FILE.read_text()))


def _cfg():
    """Load the default Config."""
    return Config()


def _subagents_dir_for_session(tmp_home: Path, session_id: str, project_dir: str) -> Path:
    """Return the subagents directory for a given session_id and project_dir.

    Calculates the project_slug by replacing non-alphanumeric chars with '-',
    matching the logic in RunningSubagents.from_session (which uses the slug as-is,
    starting with '-' from the leading '/' in the path).
    """
    project_slug = re.sub(r'[^A-Za-z0-9]', '-', project_dir)
    return tmp_home / '.claude' / 'projects' / project_slug / session_id / 'subagents'


def _build_fixture_agents(tmp_home: Path, subagents_dir: Path) -> list[str]:
    """Create 3 agents with differing token counts.

    Returns list of agent IDs in creation order.
    Agent 1: low tokens (100 in, 50 out)
    Agent 2: medium tokens (300 in, 100 out)
    Agent 3: high tokens (500 in, 150 out)
    """
    agent_ids = []

    # Agent 1: Explore with low token count
    agent_id_1 = 'agent-explore-1'
    jsonl_lines_1 = [
        '{"event": "start"}\n',
        _assistant_line('msg-1', input_tokens=100, output_tokens=50),
    ]
    _write_agent(subagents_dir, agent_id_1, agent_type='Explore',
                 description='find X', jsonl_lines=jsonl_lines_1)
    agent_ids.append(agent_id_1)

    # Agent 2: Write with medium token count
    agent_id_2 = 'agent-write-2'
    jsonl_lines_2 = [
        '{"event": "start"}\n',
        _assistant_line('msg-2', input_tokens=300, output_tokens=100),
    ]
    _write_agent(subagents_dir, agent_id_2, agent_type='Write',
                 description='write Y', jsonl_lines=jsonl_lines_2)
    agent_ids.append(agent_id_2)

    # Agent 3: Verify with high token count
    agent_id_3 = 'agent-verify-3'
    jsonl_lines_3 = [
        '{"event": "start"}\n',
        _assistant_line('msg-3', input_tokens=500, output_tokens=150),
    ]
    _write_agent(subagents_dir, agent_id_3, agent_type='Verify',
                 description='check Z', jsonl_lines=jsonl_lines_3)
    agent_ids.append(agent_id_3)

    return agent_ids


def test_cache_equivalence_cold_vs_warm(tmp_home: Path, frozen_clock: float) -> None:
    """Cold vs warm: two renders at the same frozen clock are field-equal when the
    cache is saved and loaded between them.

    Scenario:
    1. Phase 1 (cold): render with empty cache, save cache to disk
    2. Clear in-memory module caches (simulating a fresh process)
    3. Phase 2 (warm): load cache from disk, render again
    4. Assert RunningSubagents, ToolCounts totals & per_agent, and session_inout are equal
    """
    # Load the session to get the actual session_id and project_dir
    session_template = _session()
    session_id = session_template.session_id
    project_dir = session_template.workspace.project_dir

    # Setup: create 3 agents under the actual session_id
    subagents_dir = _subagents_dir_for_session(tmp_home, session_id, project_dir)
    _build_fixture_agents(tmp_home, subagents_dir)

    # Phase 1: Cold render with no cache
    session = _session()
    cfg = _cfg()
    cache_1 = TranscriptCache(session_id)
    view_1 = SessionView(session=session, cfg=cfg, now=frozen_clock, cache=cache_1)

    # Access fields to trigger caching
    subagents_1 = view_1.subagents
    tool_counts_1 = view_1.tool_counts
    session_inout_1 = view_1.session_inout

    # Snapshot the values
    assert subagents_1.subagents, f"Expected non-empty subagents, got: {subagents_1.subagents}"
    assert tool_counts_1 is not None

    # Save cache to disk
    cache_1.save()
    cache_file = cache_path(session_id)
    assert cache_file.exists(), "Cache file should exist after save()"

    # Phase 2: Warm render — simulate a fresh process by clearing in-memory caches
    # and reloading from disk
    _notif_tail_cache.clear()
    _tool_result_tail_cache.clear()

    # Build a fresh SessionView and reload cache from disk
    session_2 = _session()  # Fresh instance
    cfg_2 = _cfg()
    cache_2 = TranscriptCache.load(session_id)  # Load from disk

    view_2 = SessionView(session=session_2, cfg=cfg_2, now=frozen_clock, cache=cache_2)

    subagents_2 = view_2.subagents
    tool_counts_2 = view_2.tool_counts
    session_inout_2 = view_2.session_inout

    # Assert equivalence: both the direct equality and field-level inspection
    # (direct == is strong per the docstring of RunningSubagent)
    assert subagents_1 == subagents_2, (
        f"RunningSubagents should be equal:\n"
        f"  Cold:  {subagents_1.subagents}\n"
        f"  Warm:  {subagents_2.subagents}"
    )

    # Inspect first agent's fields to make failure diagnosable
    if subagents_1.subagents and subagents_2.subagents:
        agent_1 = subagents_1.subagents[0]
        agent_2 = subagents_2.subagents[0]
        assert agent_1.agent_type == agent_2.agent_type
        assert agent_1.description == agent_2.description
        assert agent_1.billed_in == agent_2.billed_in
        assert agent_1.output == agent_2.output
        assert agent_1.total_input == agent_2.total_input

    # ToolCounts: totals and per_agent map
    assert tool_counts_1 == tool_counts_2, (
        f"ToolCounts should be equal:\n"
        f"  Cold:  {tool_counts_1}\n"
        f"  Warm:  {tool_counts_2}"
    )
    assert tool_counts_1.counts == tool_counts_2.counts
    assert tool_counts_1.per_agent == tool_counts_2.per_agent
    assert tool_counts_1.lines_read == tool_counts_2.lines_read
    assert tool_counts_1.lines_changed == tool_counts_2.lines_changed

    # Session inout sums
    assert session_inout_1 == session_inout_2, (
        f"session_inout should be equal: {session_inout_1} vs {session_inout_2}"
    )


def test_cache_equivalence_partially_warm(tmp_home: Path, frozen_clock: float) -> None:
    """Partially warm variant: after cold + save, append new content to one agent,
    then run a fully-cold parse over the new state. The partially-warm result must
    equal the fully-cold result — that is the guarantee.

    Scenario:
    1. Phase 1 (cold): render with empty cache, save
    2. Phase 2 (append): append new JSONL lines to agent 2, changing its mtime
    3. Phase 3 (partially-warm): render with warm cache (agent 1, 3 cached; agent 2 re-parsed)
    4. Phase 4 (fully-cold): render over the new state with empty cache
    5. Assert phases 3 and 4 produce identical results
    """
    # Load the session to get the actual session_id and project_dir
    session_template = _session()
    session_id = session_template.session_id
    project_dir = session_template.workspace.project_dir

    # Setup: create 3 agents under the actual session_id
    subagents_dir = _subagents_dir_for_session(tmp_home, session_id, project_dir)
    agent_ids = _build_fixture_agents(tmp_home, subagents_dir)

    # Phase 1: Cold render
    session_1 = _session()
    cfg_1 = _cfg()
    cache_1 = TranscriptCache(session_id)
    view_1 = SessionView(session=session_1, cfg=cfg_1, now=frozen_clock, cache=cache_1)

    _ = view_1.subagents
    _ = view_1.tool_counts
    _ = view_1.session_inout

    cache_1.save()

    # Phase 2: Append new content to agent 2 (the medium-token agent)
    # Wait a bit to ensure mtime changes
    time.sleep(0.02)
    agent_2_jsonl = subagents_dir / f'{agent_ids[1]}.jsonl'
    additional_line = _assistant_line('msg-2-bis', input_tokens=50, output_tokens=20)
    agent_2_jsonl.write_text(agent_2_jsonl.read_text() + additional_line)

    # Phase 3: Partially-warm render (load cache from disk, agent 2 re-parses due to mtime change)
    _notif_tail_cache.clear()
    _tool_result_tail_cache.clear()

    session_3 = _session()
    cfg_3 = _cfg()
    cache_3 = TranscriptCache.load(session_id)
    view_3 = SessionView(session=session_3, cfg=cfg_3, now=frozen_clock, cache=cache_3)

    subagents_3 = view_3.subagents
    tool_counts_3 = view_3.tool_counts
    session_inout_3 = view_3.session_inout

    # Phase 4: Fully-cold render over the appended state (empty in-process cache)
    _notif_tail_cache.clear()
    _tool_result_tail_cache.clear()

    session_4 = _session()
    cfg_4 = _cfg()
    cache_4 = TranscriptCache(session_id)  # Fresh empty cache
    view_4 = SessionView(session=session_4, cfg=cfg_4, now=frozen_clock, cache=cache_4)

    subagents_4 = view_4.subagents
    tool_counts_4 = view_4.tool_counts
    session_inout_4 = view_4.session_inout

    # Assert equivalence: partially-warm == fully-cold
    assert subagents_3 == subagents_4, (
        f"RunningSubagents mismatch (partially-warm vs fully-cold):\n"
        f"  Phase 3: {subagents_3.subagents}\n"
        f"  Phase 4: {subagents_4.subagents}"
    )

    assert tool_counts_3 == tool_counts_4, (
        f"ToolCounts mismatch (partially-warm vs fully-cold):\n"
        f"  Phase 3: {tool_counts_3}\n"
        f"  Phase 4: {tool_counts_4}"
    )
    assert tool_counts_3.per_agent == tool_counts_4.per_agent

    assert session_inout_3 == session_inout_4, (
        f"session_inout mismatch (partially-warm vs fully-cold):\n"
        f"  Phase 3: {session_inout_3}\n"
        f"  Phase 4: {session_inout_4}"
    )
