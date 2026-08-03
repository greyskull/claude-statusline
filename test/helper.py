import datetime
import re

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')


def strip_ansi(s: str) -> str:
    return _ANSI_RE.sub('', s)


def iso_ts(epoch: float) -> str:
    '''An epoch as the UTC ISO-8601 string Claude Code writes into transcripts.

    Fixture timestamps must be derived from the fixture's own clock: a
    hardcoded literal paired with a wall-clock st_mtime makes the transcript
    look like it was written days after the terminal signal, which
    RunningSubagents treats (correctly) as a stale signal.
    '''
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
