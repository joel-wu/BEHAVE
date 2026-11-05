"""Node-local admission for a provider's account/model request-rate quota.

Counting and generation share the same gate. Waiting neither occupies a tool CPU
lease nor retries an HTTP request. Profiles select the observed provider limit;
there is no default rate cap.
"""

from dataclasses import asdict, dataclass
import fcntl
import math
from pathlib import Path
import time

from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json, fields, parse_json


@dataclass(frozen=True)
class RequestRate:
    directory: str
    requests_per_minute: int

    def __post_init__(self):
        if (type(self.directory) is not str or not Path(self.directory).is_absolute()
                or Path(self.directory).parent == Path(self.directory)):
            raise ValueError('request-rate directory must be an absolute task directory')
        if type(self.requests_per_minute) is not int or self.requests_per_minute < 1:
            raise ValueError('requests_per_minute must be a positive integer')

    def as_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        return cls(**fields(value, {'directory', 'requests_per_minute'}))


def wait_for_request(endpoint):
    policy = endpoint.rate_limit
    if policy is None:
        return
    key = BlobRef.of(canonical_json({'provider': endpoint.provider, 'model': endpoint.model,
                                   'credential': endpoint.credential.as_dict()})).sha256
    directory = Path(policy.directory)
    directory.mkdir(parents=True, exist_ok=True)
    state = directory / (key + '.json')
    with (directory / (key + '.lock')).open('a+b') as lock:
        while True:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                now = time.time()
                next_at = parse_json(state.read_bytes())['next_at'] if state.exists() else now
                if type(next_at) not in (int, float) or not math.isfinite(next_at):
                    raise ValueError('invalid request-rate state')
                delay = next_at - now
                if delay <= 0:
                    temporary = state.with_suffix('.tmp')
                    temporary.write_bytes(canonical_json({'next_at': now + 60 / policy.requests_per_minute}))
                    temporary.replace(state)
                    return
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
            time.sleep(min(delay, 60))
