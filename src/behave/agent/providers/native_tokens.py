"""Exact IDs from explicitly selected local chat extensions, never retokenization.

The operator pins the trusted server's checkpoint/tokenizer/template identities.
This binds recorded bytes and wire semantics, not remote server attestation.
SGLang 0.5.18 and vLLM 0.28.0 use different field locations. No dialect guessing,
prompt reconstruction, fabricated EOS or padding of missing log probabilities.
"""

from dataclasses import dataclass, replace
from sys import float_info

from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, canonical_json, fields, metadata, parse_json, thaw_json, version

from ..model import ModelReply, ModelRequest


@dataclass(frozen=True, slots=True)
class NativeChatTokens:
    runtime: str
    checkpoint: BlobRef
    tokenizer: BlobRef
    template: BlobRef
    continuation: dict | None = None

    def __post_init__(self):
        if self.runtime not in {"sglang", "sglang-slime", "vllm"}:
            raise ValueError("select an explicit native chat token dialect")
        if any(type(ref) is not BlobRef for ref in (self.checkpoint, self.tokenizer, self.template)):
            raise TypeError("native token provenance requires content identities")
        if self.continuation is not None:
            if self.runtime not in {"sglang", "sglang-slime"}:
                raise ValueError("native continuation requires SGLang input_ids support")
            config = fields(thaw_json(self.continuation), {"chat_template", "eos_token", "eos_token_id"})
            if (any(type(config[k]) is not str or not config[k] for k in ("chat_template", "eos_token"))
                    or type(config["eos_token_id"]) is not int or config["eos_token_id"] < 0):
                raise ValueError("native continuation requires the model template and EOS definition")
            object.__setattr__(self, "continuation", metadata(config))

    @property
    def adapter(self) -> str:
        return f"behave.native-chat-{self.runtime}/1"

    def as_dict(self) -> dict:
        return {
            "schema": "behave.native-chat-tokens/1" if self.continuation is None else "behave.native-chat-tokens/2",
            "runtime": self.runtime,
            **{key: getattr(self, key).as_dict() for key in ("checkpoint", "tokenizer", "template")},
            **({"continuation": thaw_json(self.continuation)} if self.continuation is not None else {}),
        }

    @classmethod
    def from_dict(cls, value):
        continued = "continuation" in value
        row = fields(value, {"schema", "runtime", "checkpoint", "tokenizer", "template"}
                     | ({"continuation"} if continued else set()))
        version(row["schema"], "behave.native-chat-tokens/2" if continued else "behave.native-chat-tokens/1")
        return cls(row["runtime"], *(BlobRef.from_dict(row[key]) for key in ("checkpoint", "tokenizer", "template")),
                   continuation=row.get("continuation"))

    def parameters(self) -> dict:
        if self.runtime == "sglang-slime":
            # Slime's pinned 0.5.15 wire predates response_token_ids. Exact
            # generated IDs are the IDs in output_token_logprobs, including EOS.
            return {"return_prompt_token_ids": True, "return_meta_info": True,
                    "logprobs": True, "top_logprobs": 0}
        options = {"return_token_ids": True, "logprobs": True, "top_logprobs": 0}
        if self.runtime == "sglang":
            options["return_meta_info"] = True
        else:
            options["return_tokens_as_token_ids"] = True
        return options


def _require(condition: bool) -> None:
    if not condition:
        raise RecordError("native-token-evidence-invalid", "native IDs or log probabilities disagree")


def _ids(value) -> list[int]:
    _require(
        type(value) is list
        and len(value) > 0
        and all(type(token) is int and token >= 0 for token in value)
    )
    return value


def decode_tokens(config: NativeChatTokens, request: ModelRequest, raw: bytes) -> dict:
    """Decode a response already validated by the ordinary chat normalizer."""
    return decode_token_body(config, request, parse_json(raw))


def decode_token_body(config: NativeChatTokens, request: ModelRequest, body: dict) -> dict:
    """Decode native IDs from the same strictly parsed body as chat normalization."""
    choices = body.get("choices")
    _require(type(choices) is list and len(choices) == 1 and type(choices[0]) is dict)
    choice = choices[0]
    if config.runtime == "sglang-slime":
        info = choice.get("meta_info")
        rows = info.get("output_token_logprobs") if type(info) is dict else None
        _require(type(rows) is list and all(type(row) is list and len(row) == 3 for row in rows))
        inputs, outputs = _ids(choice.get("prompt_token_ids")), _ids([row[1] for row in rows])
    elif config.runtime == "sglang":
        inputs, outputs = _ids(choice.get("prompt_token_ids")), _ids(choice.get("response_token_ids"))
    else:
        inputs, outputs = _ids(body.get("prompt_token_ids")), _ids(choice.get("token_ids"))
    if request.context_tokens is not None:
        _require(len(inputs) + len(outputs) <= request.context_tokens)
    # Usage is accounting, not the sampled sequence. Servers can count EOS or
    # cached tokens differently; keep complete native IDs/logprobs regardless.
    probabilities = None
    if config.runtime in {"sglang", "sglang-slime"}:
        info = choice.get("meta_info")
        rows = info.get("output_token_logprobs") if type(info) is dict else None
        if rows is not None:
            _require(type(rows) is list and len(rows) == len(outputs))
            _require(
                all(
                    type(row) is list and len(row) == 3 and type(row[1]) is int and row[1] == token
                    for row, token in zip(rows, outputs, strict=True)
                )
            )
            probabilities = [row[0] for row in rows]
    else:
        info = choice.get("logprobs")
        rows = info.get("content") if type(info) is dict else None
        if rows is not None:
            _require(type(rows) is list and len(rows) == len(outputs))
            _require(
                all(
                    type(row) is dict and row.get("token") == f"token_id:{token}"
                    for row, token in zip(rows, outputs, strict=True)
                )
            )
            probabilities = [row.get("logprob") for row in rows]
    signals = {}
    if probabilities is not None:
        _require(all(type(value) in (int, float) and -float_info.max <= value <= 0 for value in probabilities))
        # vLLM's public chat wire clamps these values. Preserve tokens but do not
        # advertise exact old-policy probabilities when the sentinel is present.
        if config.runtime != "vllm" or all(value > -9999 for value in probabilities):
            signals["old_log_probs"] = probabilities
    return {
        "schema": "behave.token-trace/1",
        "adapter": config.adapter,
        "request": BlobRef.of(canonical_json(request.as_dict())).as_dict(),
        **{key: getattr(config, key).as_dict() for key in ("checkpoint", "tokenizer", "template")},
        "input_ids": inputs,
        "response_ids": outputs,
        "response_mask": [1] * len(outputs),
        "signals": signals,
    }


def attach_tokens(config: NativeChatTokens, request: ModelRequest, reply):
    """Token metadata failure cannot invalidate an otherwise usable inference reply."""
    if type(reply) is not ModelReply:
        return reply
    try:
        data = decode_tokens(config, request, reply.raw)
    except (RecordError, ValueError, TypeError, KeyError):
        data = {
            "schema": "behave.native-token-unavailable/1",
            "adapter": config.adapter,
            "reason": "native-token-evidence-invalid",
        }
    return replace(reply, token_data=data)
