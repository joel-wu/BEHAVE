"""Reward + original retained/excluded turns, before any training algorithm.

No retokenization, synthetic log probabilities, advantage calculation or GPU
dependency. Only the result's sealed solver prefix is read; later journal writes
cannot change an exported candidate. Text-only replies remain unavailable for
training until a serving adapter supplies and validates actual token data.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from importlib.metadata import version as package_version
from pathlib import Path
from sys import float_info
from types import MappingProxyType
from typing import Protocol

from behave.agent.model import ModelReply, ModelRequest
from behave.agent.providers.chat_wire import normalize_response, request_body
from behave.agent.providers.local_chat import LocalChatEndpoint
from behave.agent.providers.native_tokens import decode_token_body
from behave.agent.trajectory import CostLedger, Trajectory
from behave.core.artifacts import BlobRef, ContentReader, FileRef, read_verified
from behave.core.file_input import read_regular_file
from behave.core.records import (
    RecordError,
    array,
    canonical_json,
    fields,
    metadata,
    parse_json,
    text,
    thaw_json,
    version,
)
from behave.improve.materials import raw_documents
from behave.task_execution.accounting import load_accounting
from behave.task_execution.result import TaskResult


@dataclass(frozen=True, slots=True)
class TrainingCandidate:
    result_ref: BlobRef
    result: TaskResult
    # Native training reads the recorded result and tokens directly. Full
    # trajectory/billing views are populated only by the offline audit reader.
    trajectory: Trajectory | None = None
    billing: CostLedger | None = None

    @property
    def reward(self) -> float:
        return self.result.final_score


def load_candidate(reader: ContentReader, result_ref: BlobRef, *, accounting=None) -> TrainingCandidate:
    """Settle unresolved scores as zero without changing the execution record."""
    result = TaskResult.from_bytes(read_verified(reader, result_ref))
    if accounting is None:
        accounting = load_accounting(reader, result)
    return TrainingCandidate(result_ref, result, accounting.trajectory, accounting.billing)


@dataclass(frozen=True, slots=True)
class RawEncoding:
    tokenizer: BlobRef
    implementation: str
    bos_id: int
    eos_id: int
    context_tokens: int

    def __post_init__(self):
        if type(self.tokenizer) is not BlobRef:
            raise TypeError("Raw encoding requires a pinned tokenizer")
        text(self.implementation, "tokenizer implementation")
        if any(type(value) is not int or value < 0 for value in (self.bos_id, self.eos_id)):
            raise ValueError("explicit nonnegative BOS/EOS token IDs required")
        if type(self.context_tokens) is not int or self.context_tokens < 2:
            raise ValueError("Raw context must hold at least one context token and one target")

    def as_dict(self):
        return {"tokenizer": self.tokenizer.as_dict(), "implementation": self.implementation,
                "bos_id": self.bos_id, "eos_id": self.eos_id, "context_tokens": self.context_tokens}

    @classmethod
    def from_dict(cls, value):
        row = fields(value, set(cls.__dataclass_fields__))
        return cls(**{**row, "tokenizer": BlobRef.from_dict(row["tokenizer"])})


@dataclass(frozen=True, slots=True)
class RawSegment:
    document: FileRef
    offset: int
    tokens: tuple[int, ...]

    def __post_init__(self):
        if type(self.document) is not FileRef:
            raise TypeError("Raw segments require their original document identity")
        if type(self.offset) is not int or self.offset < 0:
            raise ValueError("Raw segment context offset must be nonnegative")
        if (type(self.tokens) is not tuple or len(self.tokens) < 2 or
                any(type(token) is not int or token < 0 for token in self.tokens)):
            raise ValueError("Raw segments need context and nonnegative target token IDs")

    @property
    def targets(self):
        return len(self.tokens) - 1

    def as_dict(self):
        return {"document": self.document.as_dict(), "offset": self.offset, "tokens": list(self.tokens)}

    @classmethod
    def from_dict(cls, value):
        row = fields(value, {"document", "offset", "tokens"})
        return cls(FileRef.from_dict(row["document"]), row["offset"], tuple(row["tokens"]))


@dataclass(frozen=True, slots=True)
class RawCorpus:
    materials: BlobRef
    encoding: RawEncoding
    segments: tuple[RawSegment, ...]

    def to_bytes(self):
        return canonical_json({"schema": "behave.raw-token-corpus/1", "materials": self.materials.as_dict(),
            "encoding": self.encoding.as_dict(), "segments": [segment.as_dict() for segment in self.segments]})

    @classmethod
    def from_bytes(cls, payload):
        row = fields(parse_json(payload), {"schema", "materials", "encoding", "segments"})
        version(row["schema"], "behave.raw-token-corpus/1")
        result = cls(BlobRef.from_dict(row["materials"]), RawEncoding.from_dict(row["encoding"]),
                     tuple(RawSegment.from_dict(segment) for segment in row["segments"]))
        if result.to_bytes() != payload:
            raise RecordError("record-not-canonical", "Raw token corpus must be canonical")
        return result


def segment_document(document: FileRef, tokens: tuple[int, ...], encoding: RawEncoding) -> tuple[RawSegment, ...]:
    sequence = (encoding.bos_id, *tokens, encoding.eos_id)
    stride = encoding.context_tokens - 1
    return tuple(RawSegment(document, offset, sequence[offset:offset + encoding.context_tokens])
                 for offset in range(0, len(sequence) - 1, stride))


def tokenize_materials(reader: ContentReader, materials: BlobRef, encoding: RawEncoding) -> RawCorpus:
    # Optional native dependency; importing pipeline records does not import
    # Torch, allocate a model, connect to a provider or fetch a tokenizer.
    from tokenizers import Tokenizer

    if encoding.implementation != "tokenizers/" + package_version("tokenizers"):
        raise RecordError("raw-tokenizer-version-mismatch", "tokenizer implementation differs from the pinned policy")
    tokenizer = Tokenizer.from_str(read_verified(reader, encoding.tokenizer).decode("utf-8"))
    # Serialized tokenizer defaults may carry an old inference window/padding
    # policy. Chunk the complete source ourselves; neither silently drop tokens
    # nor turn padding into continued-learning targets.
    tokenizer.no_truncation()
    tokenizer.no_padding()
    vocabulary = set(tokenizer.get_vocab().values())
    if encoding.bos_id not in vocabulary or encoding.eos_id not in vocabulary:
        raise RecordError("raw-special-token-invalid", "BOS/EOS must be entries in the pinned tokenizer")
    segments = []
    for file in raw_documents(reader, materials):
        tokens = tuple(tokenizer.encode(file.data.decode("utf-8"), add_special_tokens=False).ids)
        segments.extend(segment_document(file.reference, tokens, encoding))
    return RawCorpus(materials, encoding, tuple(segments))


def load_raw_corpus(reader: ContentReader, reference: BlobRef) -> RawCorpus:
    corpus = RawCorpus.from_bytes(read_verified(reader, reference))
    if tokenize_materials(reader, corpus.materials, corpus.encoding) != corpus:
        raise RecordError("raw-token-evidence-mismatch", "Raw tokens differ from the frozen external documents")
    return corpus


def native_train_data(samples: tuple[RawSegment, ...], *, first_index: int = 0) -> dict:
    """Slime's native SFT inputs, without invented rewards or old-policy logits.

    The Raw adapter must select ``sft_loss``, token-mean reduction and disabled
    advantage computation. Each segment is a separate sequence: attention never
    crosses document boundaries. This function does not run an optimizer.
    """
    if not samples or any(type(sample) is not RawSegment for sample in samples):
        raise ValueError("a Raw update requires actual source segments")
    if type(first_index) is not int or first_index < 0:
        raise ValueError("sample identity offset must be nonnegative")
    indices = list(range(first_index, first_index + len(samples)))
    return {"tokens": [list(sample.tokens) for sample in samples],
            "response_lengths": [sample.targets for sample in samples],
            "loss_masks": [[1] * sample.targets for sample in samples],
            "rollout_mask_sums": [sample.targets for sample in samples],
            "sample_indices": indices, "rollout_ids": indices,
            "truncated": [0] * len(samples)}


SIGNALS = frozenset({"old_log_probs", "teacher_log_probs", "values"})


@dataclass(frozen=True, slots=True)
class TokenTrace:
    adapter: str
    request: BlobRef
    checkpoint: BlobRef
    tokenizer: BlobRef
    template: BlobRef
    input_ids: tuple[int, ...]
    response_ids: tuple[int, ...]
    response_mask: tuple[int, ...]
    signals: MappingProxyType

    def __post_init__(self):
        text(self.adapter, "serving adapter")
        if any(type(ref) is not BlobRef for ref in (self.request, self.checkpoint, self.tokenizer, self.template)):
            raise TypeError("token provenance requires content identities")
        if not self.response_ids:
            raise ValueError("token trace requires generated tokens")
        for tokens in (self.input_ids, self.response_ids):
            if type(tokens) is not tuple or any(type(t) is not int or t < 0 for t in tokens):
                raise ValueError("tokens require nonnegative integer ids")
        if (
            type(self.response_mask) is not tuple
            or len(self.response_mask) != len(self.response_ids)
            or any(type(m) is not int or m not in (0, 1) for m in self.response_mask)
        ):
            raise ValueError("loss mask must be binary and response-aligned")
        object.__setattr__(self, "signals", metadata(self.signals))
        if not set(self.signals) <= SIGNALS:
            raise ValueError("unknown training signal")
        for name, values in self.signals.items():
            if (
                type(values) is not tuple
                or len(values) != len(self.response_ids)
                or any(type(v) not in (float, int) or not -float_info.max <= v <= float_info.max for v in values)
            ):
                raise ValueError("training signals must be finite and response-aligned")
            if name.endswith("log_probs") and any(v > 0 for v in values):
                raise ValueError("log probabilities cannot be positive")

    @property
    def full_loss_mask(self):
        return (0,) * len(self.input_ids) + self.response_mask

    def as_dict(self):
        return {
            "schema": "behave.token-trace/1",
            "adapter": self.adapter,
            **{key: getattr(self, key).as_dict() for key in ("request", "checkpoint", "tokenizer", "template")},
            **{key: list(getattr(self, key)) for key in ("input_ids", "response_ids", "response_mask")},
            "signals": thaw_json(self.signals),
        }

    @classmethod
    def from_dict(cls, value):
        row = fields(value, {"schema", *cls.__dataclass_fields__})
        version(row["schema"], "behave.token-trace/1")
        return cls(
            row["adapter"],
            *(BlobRef.from_dict(row[k]) for k in ("request", "checkpoint", "tokenizer", "template")),
            *(tuple(array(row[k])) for k in ("input_ids", "response_ids", "response_mask")),
            row["signals"],
        )


class TokenVerifier(Protocol):
    """Trusted, explicitly installed adapter; not candidate code or a model tool."""

    identity: str

    def verify(self, trace: TokenTrace, request: ModelRequest, raw_response: bytes) -> dict | None:
        """Check generation provenance; optionally return already-read serving metadata."""
        ...


@dataclass(frozen=True, slots=True)
class TrainingRequirements:
    adapter: str
    checkpoint: BlobRef
    tokenizer: BlobRef
    template: BlobRef
    required_signals: tuple[str, ...] = ()

    def __post_init__(self):
        text(self.adapter, "training serving adapter")
        if any(type(ref) is not BlobRef for ref in (self.checkpoint, self.tokenizer, self.template)):
            raise TypeError("training requirements need pinned provenance")
        if (
            type(self.required_signals) is not tuple
            or len(set(self.required_signals)) != len(self.required_signals)
            or not set(self.required_signals) <= SIGNALS
        ):
            raise ValueError("invalid training capability requirements")

    def as_dict(self):
        return {
            "schema": "behave.training-requirements/1",
            "adapter": self.adapter,
            **{key: getattr(self, key).as_dict() for key in ("checkpoint", "tokenizer", "template")},
            "required_signals": list(self.required_signals),
        }

    @classmethod
    def from_bytes(cls, payload):
        row = fields(parse_json(payload), {"schema", *cls.__dataclass_fields__})
        version(row["schema"], "behave.training-requirements/1")
        result = cls(
            row["adapter"],
            *(BlobRef.from_dict(row[k]) for k in ("checkpoint", "tokenizer", "template")),
            tuple(array(row["required_signals"])),
        )
        if canonical_json(result.as_dict()) != payload:
            raise RecordError("record-not-canonical", "training requirements must be canonical")
        return result


class NativeChatVerifier:
    def __init__(self, endpoint: LocalChatEndpoint, *history: LocalChatEndpoint):
        endpoints = (endpoint, *history)
        if any(type(item) is not LocalChatEndpoint or item.tokens is None for item in endpoints):
            raise ValueError("native token verification requires original token-enabled endpoints")
        self.identity = endpoint.tokens.adapter
        if any(item.tokens.adapter != self.identity for item in endpoints):
            raise ValueError("one verifier history must use one native token adapter")
        self.endpoints = MappingProxyType({item.identity: item for item in endpoints})

    @classmethod
    def from_files(cls, paths: Iterable[Path]):
        endpoints = []
        for path in paths:
            payload = read_regular_file(path)
            endpoints.append(LocalChatEndpoint.from_dict(parse_json(payload)))
        if not endpoints:
            raise ValueError("at least one native endpoint profile is required")
        return cls(*endpoints)

    @classmethod
    def from_file(cls, path: Path):
        return cls.from_files((path,))

    def require(self, requirements: TrainingRequirements) -> None:
        if requirements.adapter != self.identity or not any(
            all(getattr(requirements, key) == getattr(endpoint.tokens, key)
                for key in ("checkpoint", "tokenizer", "template"))
            for endpoint in self.endpoints.values()
        ):
            raise RecordError("training-verifier-mismatch", "no approved endpoint matches the training token requirements")

    def verify(self, trace: TokenTrace, request: ModelRequest, raw_response: bytes) -> dict:
        # The normal request/response validators bind model, endpoint profile,
        # history, tool calls, finish reason and raw usage before token checking.
        endpoint = self.endpoints.get(request.model_policy.get("provider"))
        if endpoint is None:
            raise ValueError("model policy differs from approved endpoints")
        request_body(endpoint, request)
        try:
            body = parse_json(raw_response)
        except ValueError as error:
            raise RecordError("native-token-response-invalid", "response is not eligible native generation") from error
        reply = normalize_response(endpoint, 200, raw_response, body)
        if type(reply) is not ModelReply or reply.training_exclusion is not None:
            raise RecordError("native-token-response-invalid", "response is not eligible native generation")
        expected = decode_token_body(endpoint.tokens, request, body)
        # Compare typed IDs directly. Serializing the entire cumulative prompt
        # twice is unnecessary. Preserve exact numeric representations for the
        # response signals, including integer/float and signed-zero distinctions.
        if (TokenTrace.from_dict(expected) != trace or
                canonical_json(expected["signals"]) != canonical_json(thaw_json(trace.signals))):
            raise RecordError("native-token-trace-mismatch", "trace differs from the recorded native response")
        return {"endpoint": endpoint.identity, "prefix_cache": prefix_cache_usage(body, len(trace.input_ids))}


def prefix_cache_usage(body: dict, prompt_tokens: int) -> dict | None:
    """Optional telemetry read from the already parsed native response."""
    usage = body.get("usage")
    details = usage.get("prompt_tokens_details") if type(usage) is dict else None
    cached = details.get("cached_tokens") if type(details) is dict else None
    choices = body.get("choices")
    if cached is None and details is None and type(choices) is list and len(choices) == 1:
        info = choices[0].get("meta_info") if type(choices[0]) is dict else None
        if type(info) is dict and info.get("prompt_tokens") == prompt_tokens:
            cached = info.get("cached_tokens")
    if type(cached) is not int or not 0 <= cached <= prompt_tokens:
        return None
    return {"cached_tokens": cached, "total_prompt_tokens": prompt_tokens}


@dataclass(frozen=True, slots=True)
class TurnExample:
    turn: int
    request: BlobRef
    response: BlobRef
    status: str
    reason: str | None
    tokens: TokenTrace | None = None
    # Transient serving metadata, obtained during token verification. It is not
    # another persisted claim and does not change historical report bytes.
    capture: dict | None = None


@dataclass(frozen=True, slots=True)
class RolloutExamples:
    candidate: TrainingCandidate
    turns: tuple[TurnExample, ...]
    requirements: TrainingRequirements

    @property
    def reward(self):
        return self.candidate.reward

    @property
    def eligible(self):
        return tuple(t for t in self.turns if t.status == "eligible")

    def as_dict(self):
        return {
            "schema": "behave.rollout-examples/1",
            "result": self.candidate.result_ref.as_dict(),
            "reward": self.reward,
            "requirements": self.requirements.as_dict(),
            "turns": [
                {
                    "turn": t.turn,
                    "request": t.request.as_dict(),
                    "response": t.response.as_dict(),
                    "status": t.status,
                    "reason": t.reason,
                    "tokens": None if t.tokens is None else t.tokens.as_dict(),
                }
                for t in self.turns
            ],
        }


def prepare_examples(
    reader: ContentReader, result: BlobRef, requirements: TrainingRequirements, verifier: TokenVerifier | None = None,
    *, accounting=None,
) -> RolloutExamples:
    if type(requirements) is not TrainingRequirements:
        raise TypeError("training export requires explicit capabilities and provenance")
    if verifier is not None and verifier.identity != requirements.adapter:
        raise ValueError("token verifier differs from the required serving adapter")
    candidate = (load_candidate(reader, result) if accounting is None
                 else load_candidate(reader, result, accounting=accounting))
    turns = []
    seen = set()
    for turn in candidate.trajectory.turns:
        if turn.turn in seen:
            raise RecordError("training-turn-duplicate", "a rollout cannot train twice on one model turn")
        seen.add(turn.turn)
        status, reason, tokens, capture = "unavailable", "token-data-missing", None, None
        if turn.exclusion is not None:
            status, reason = "excluded", turn.exclusion
        elif turn.token_data is not None:
            row = thaw_json(turn.token_data)
            if row.get("schema") == "behave.native-token-unavailable/1":
                unavailable = fields(row, {"schema", "adapter", "reason"})
                reason = text(unavailable["reason"], "native token unavailability")
            elif row.get("schema") != "behave.token-trace/1":
                reason = "token-schema-unsupported"
            else:
                trace = TokenTrace.from_dict(row)
                if trace.request != turn.request:
                    raise RecordError("training-request-mismatch", "token record belongs to another model request")
                if any(
                    getattr(trace, k) != getattr(requirements, k)
                    for k in ("adapter", "checkpoint", "tokenizer", "template")
                ):
                    reason = "token-provenance-mismatch"
                elif not set(requirements.required_signals) <= set(trace.signals):
                    reason = "training-signals-missing"
                elif verifier is None:
                    reason = "token-verifier-unavailable"
                else:
                    request = ModelRequest.from_dict(parse_json(read_verified(reader, turn.request)))
                    capture = verifier.verify(trace, request, read_verified(reader, turn.raw_response))
                    tokens = trace
                    if not any(trace.response_mask):
                        status, reason = "excluded", "no-eligible-response-tokens"
                    else:
                        status, reason = "eligible", None
        turns.append(TurnExample(turn.turn, turn.request, turn.response, status, reason, tokens, capture))
    return RolloutExamples(candidate, tuple(turns), requirements)
