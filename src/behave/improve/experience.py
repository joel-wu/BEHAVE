"""Two private Analyst memory topics using ordinary workspace snapshots.

The historical names describe the notes, not Builder/solver access permissions.
"""

from dataclasses import asdict, dataclass, replace

from behave.agent.providers.local_chat import LocalChatEndpoint
from behave.agent.workspace import WorkspaceSnapshot
from behave.core.artifacts import BlobRef, FileData, FileRef, read_verified
from behave.core.records import RecordError, canonical_json, fields, parse_json, thaw_json

from .training_connection import model_command

EXPERIENCE_VIEWS = ("construction", "solver")


def experience_files(value):
    """Encode a complete replacement, not an append-only transcript."""
    row = fields(value, set(EXPERIENCE_VIEWS))
    files = []
    for name in EXPERIENCE_VIEWS:
        content = row[name]
        if type(content) is not str:
            raise TypeError("experience views must be text")
        data = content.encode("utf-8")
        files.append(FileData(FileRef(f"experience/{name}.md", BlobRef.of(data)), data))
    return tuple(files)


def experience_snapshot(files):
    return WorkspaceSnapshot(tuple(file.reference for file in files))


def load_experience(reader, reference):
    """Load only the two declared views; the caller decides who may see them."""
    snapshot = WorkspaceSnapshot.from_bytes(read_verified(reader, reference))
    expected = {f"experience/{name}.md" for name in EXPERIENCE_VIEWS}
    if {file.path for file in snapshot.files} != expected or snapshot.symlinks:
        raise RecordError("experience-views-invalid", "experience requires construction and solver text views")
    files = tuple(FileData(file, read_verified(reader, file.content)) for file in snapshot.files)
    for file in files:
        file.data.decode("utf-8")
    return files


@dataclass(frozen=True, slots=True)
class ExperiencePolicy:
    delta_bytes: int = 2048
    memory_bytes: int = 16384
    input_bytes: int = 131072
    max_turns: int = 2
    request_seconds: int = 600
    context_tokens: int = 65536
    version: int = 1
    max_output_tokens: int = 4096
    legacy_chunk_bytes: int = 8192

    def __post_init__(self):
        if any(type(v) is not int or v < 1 for v in asdict(self).values()):
            raise ValueError("experience budgets must be positive integers")
        if self.version not in (1, 2):
            raise ValueError("unknown experience policy version")
        if self.version == 2 and (self.legacy_chunk_bytes < 4 or
                6 * self.legacy_chunk_bytes + 4096 > self.input_bytes):
            raise ValueError("legacy chunk cannot fit the serialized input budget")
        if self.input_bytes < 6 * (self.memory_bytes + self.delta_bytes) + 4096:
            # JSON escaping can expand one input byte to six bytes. A single
            # complete delta must always fit; never truncate a lesson silently.
            raise ValueError("experience input budget cannot fit memory and one delta")

    def as_dict(self):
        row = asdict(self)
        if self.version == 1:
            for name in ("version", "max_output_tokens", "legacy_chunk_bytes"):
                row.pop(name)
        return row

    @classmethod
    def from_dict(cls, value):
        names = set(cls.__dataclass_fields__)
        if "version" not in value:
            names -= {"version", "max_output_tokens", "legacy_chunk_bytes"}
        elif value["version"] != 2:
            raise ValueError("explicit experience policy must be version 2")
        return cls(**fields(value, names))


def bounded_views(value, limit):
    files = experience_files(value)
    size = sum(len(f.data) for f in files)
    if size > limit:
        raise RecordError("experience-size-limit",
            f"Submitted {size} UTF-8 bytes. Both views together must fit {limit} UTF-8 bytes. "
            f"Keep only the most useful lessons; aim below {max(1, limit // 2)} bytes and resubmit.")
    return files


def delta_files(value, limit):
    return tuple(FileData(FileRef(f.reference.path.replace("experience/", "experience_delta/"),
                                 f.reference.content), f.data) for f in bounded_views(value, limit))


def load_delta(reader, reference, limit):
    snapshot = WorkspaceSnapshot.from_bytes(read_verified(reader, reference))
    expected = {f"experience_delta/{name}.md" for name in EXPERIENCE_VIEWS}
    if snapshot.symlinks or {f.path for f in snapshot.files} != expected:
        raise RecordError("experience-delta-invalid", "expected an incremental experience snapshot")
    value = {f.path.rsplit("/", 1)[-1][:-3]: read_verified(reader, f.content).decode("utf-8")
             for f in snapshot.files}
    return delta_files(value, limit)


def views(files):
    return {f.reference.path.rsplit("/", 1)[-1][:-3]: f.data.decode("utf-8") for f in files}


def memory_value(reader, reference, policy):
    value = {name: "" for name in EXPERIENCE_VIEWS} if reference is None else views(load_experience(reader, reference))
    bounded_views(value, policy.memory_bytes)
    return value


def merge_payload(reader, prior, proposals, policy, *, compact_legacy=False):
    # A one-time, no-delta compaction can read legacy memory larger than the
    # active-memory budget. Bound the entire serialized input, never clip it.
    if compact_legacy:
        if proposals:
            raise ValueError("legacy compaction cannot also merge new deltas")
        value = {name: "" for name in EXPERIENCE_VIEWS} if prior is None else views(load_experience(reader, prior))
    else:
        value = memory_value(reader, prior, policy)
    payload = canonical_json({"prior": value, "changes": [
        {"id": ref.sha256, **views(load_delta(reader, ref, policy.delta_bytes))} for ref in proposals]})
    if len(payload) > policy.input_bytes:
        raise RecordError("experience-input-limit", "merge payload exceeds its configured byte budget")
    return payload


def delta_batches(reader, prior, proposals, policy):
    # Reserve the worst-case serialized size of the next compact memory. It
    # changes after each batch; the initial memory's shortness is not a budget.
    reserve = 6 * policy.memory_bytes + 128
    batches, batch, used = [], [], reserve
    for ref in dict.fromkeys(proposals):
        value = views(load_delta(reader, ref, policy.delta_bytes))
        if not any(v.strip() for v in value.values()):
            continue
        size = len(canonical_json({"id": ref.sha256, **value})) + 1
        if batch and used + size > policy.input_bytes:
            batches.append(tuple(batch))
            batch, used = [], reserve
        if used + size > policy.input_bytes:
            raise RecordError("experience-input-limit", "one delta exceeds the configured merge input budget")
        batch.append(ref); used += size
    if batch:
        batches.append(tuple(batch))
    memory_value(reader, prior, policy)
    return tuple(batches)


def memory_parameters(parameters, policy):
    value = dict(thaw_json(parameters))
    value.pop("reasoning_effort", None)
    value["max_tokens"] = min(value.get("max_tokens", policy.max_output_tokens), policy.max_output_tokens)
    value["chat_template_kwargs"] = {**value.get("chat_template_kwargs", {}),
                                     "enable_thinking": False, "reasoning_effort": "low"}
    return value


def memory_job(job, policy):
    if policy.version == 1:
        return job
    command = job.model
    if command.revision.startswith("behave.local-chat/"):
        endpoint = LocalChatEndpoint.from_dict(parse_json(command.argv[-1].encode()))
        if endpoint.tokens is not None:
            raise ValueError("memory requires a generation-only chat endpoint")
        endpoint = replace(endpoint, parameters=memory_parameters(endpoint.parameters, policy))
        model = replace(endpoint.command(python=command.argv[0], package=command.argv[-2]),
                        environment=command.environment)
        provider = endpoint.identity
    elif command.revision in {"behave.training-owned-chat/1", "behave.training-owned-chat/2"}:
        approved = parse_json(command.argv[-1].encode())
        params = memory_parameters(approved["profile"]["parameters"], policy)
        model = replace(model_command(command.argv[-2], approved["profile"], approved["checkpoint"],
                                      generation_parameters=params), environment=command.environment)
        provider = model.identity
    else:
        raise ValueError("bounded memory generation requires local or training-owned chat")
    return replace(job, model=model, attempt=replace(job.attempt, model_policy={
        **thaw_json(job.attempt.model_policy), "provider": provider, "transport": model.identity}))
