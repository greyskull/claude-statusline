'''Confirms hooks/yas-prompt-hook.py writes under the new yas/state/signals/
layout, and that yas.info.subagents.read_last_prompt_ts reads the same file
back via yas.constants.last_prompt_path().
'''
import importlib.util
import io
import json
import os
import sys
from pathlib import Path

from yas.constants import last_prompt_path
from yas.info.subagents import read_last_prompt_ts

_HOOK_SCRIPT = Path(__file__).resolve().parent.parent / 'hooks' / 'yas-prompt-hook.py'


def _load_hook_module():
    mod_name = '_yas_prompt_hook_layout'
    if mod_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(mod_name, _HOOK_SCRIPT)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[mod_name]


def _run_hook(session_id: str, config_dir: Path) -> None:
    mod = _load_hook_module()
    payload = json.dumps({'session_id': session_id})
    env_backup = os.environ.copy()
    try:
        os.environ['CLAUDE_CONFIG_DIR'] = str(config_dir)
        old_stdin = sys.stdin
        sys.stdin = io.StringIO(payload)
        try:
            mod.main()
        finally:
            sys.stdin = old_stdin
    finally:
        for k in list(os.environ.keys()):
            if k not in env_backup:
                del os.environ[k]
        for k, v in env_backup.items():
            os.environ[k] = v


def test_hook_writes_new_layout_signals_path(tmp_home):
    claude_dir = tmp_home / '.claude'
    _run_hook('sess-layout', claude_dir)

    expected = claude_dir / 'yas' / 'state' / 'signals' / 'last-prompt.json'
    assert expected.is_file()
    data = json.loads(expected.read_text())
    assert 'sess-layout' in data


def test_read_last_prompt_ts_reads_new_layout_path_back(tmp_home):
    claude_dir = tmp_home / '.claude'
    _run_hook('sess-roundtrip', claude_dir)

    # last_prompt_path() reads yas.constants.CLAUDE_DIR (patched by tmp_home)
    # and must resolve to the exact file the hook just wrote.
    expected = claude_dir / 'yas' / 'state' / 'signals' / 'last-prompt.json'
    assert last_prompt_path() == expected

    ts = read_last_prompt_ts('sess-roundtrip')
    assert ts is not None
    on_disk = json.loads(expected.read_text())['sess-roundtrip']
    assert ts == on_disk
