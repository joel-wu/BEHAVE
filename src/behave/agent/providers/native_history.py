"""Append environment text to sampled IDs using the selected model's template.

Only the new suffix is tokenized. Parsed assistant messages are never used to
reconstruct sampled history. The response record stays the sole owner of that
history; requests carry a small reference, not another copy of its token array.
"""

import json
from pathlib import Path

from jinja2 import TemplateError
from jinja2.sandbox import ImmutableSandboxedEnvironment

from ..model import ModelFailure
from .context import encode_input


def model_continuation(config: dict) -> dict:
    """Read the explicitly selected tokenizer config, without guessing token IDs."""
    eos = config["eos_token"]
    eos = eos["content"] if isinstance(eos, dict) else eos
    (eos_id,) = (int(key) for key, value in config["added_tokens_decoder"].items() if value["content"] == eos)
    return {"chat_template": config["chat_template"], "eos_token": eos, "eos_token_id": eos_id}


def _raise(message):
    raise TemplateError(message)


def _tojson(value, ensure_ascii=False, indent=None, separators=None, sort_keys=False):
    # Match the model renderer's JSON filter, not Jinja's HTML-escaping filter.
    return json.dumps(value, ensure_ascii=ensure_ascii, indent=indent, separators=separators, sort_keys=sort_keys)


def append_text(config, messages, *, tools=(), template_kwargs=None, ended=True):
    """Render just the environment suffix with the model's own chat template.

    The synthetic empty turn is a rendering anchor only; none of it is sent to
    the model. A template that rewrites the anchor cannot be appended this way.
    A missing EOS after output-length truncation is an environment separator,
    never a fabricated sampled token or a new loss-bearing response.
    """
    environment = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True,
                                                extensions=["jinja2.ext.loopcontrols"])
    environment.filters["tojson"] = _tojson
    environment.globals["raise_exception"] = _raise
    template = environment.from_string(config["chat_template"])
    anchor = [{"role": "user", "content": ""}, {"role": "assistant", "content": ""}]
    kwargs = {"tools": tools, "eos_token": config["eos_token"], **(template_kwargs or {})}
    base = template.render(messages=anchor, add_generation_prompt=False, **kwargs)
    full = template.render(messages=[*anchor, *messages], add_generation_prompt=True, **kwargs)
    _, marker, after = base.rpartition(config["eos_token"])
    if not marker or not full.startswith(base) or any(m["role"] not in {"user", "tool"} for m in messages):
        raise ValueError("model template does not support append-only environment continuation")
    return ("" if ended else marker) + after + full[len(base):]


def continue_input(config, request, body, *, url, headers):
    """Resolve one producer-owned record, append new IDs, and keep old IDs exact."""
    prefix = request.native_prefix
    try:
        path = Path(prefix["objects"]) / prefix["response"]["sha256"]
        with path.open() as stream:
            tokens = json.load(stream)["token_data"]
        inputs = tokens["input_ids"] + tokens["response_ids"]
        suffix = append_text(config, body["messages"][prefix["message_count"]:],
            tools=body.get("tools", ()), template_kwargs=body.get("chat_template_kwargs"),
            ended=inputs[-1] == config["eos_token_id"])
    except (OSError, ValueError, TypeError, KeyError, TemplateError):
        return ModelFailure("permanent", "native-continuation-unavailable")
    encoded = encode_input(url, {"model": body["model"], "prompt": suffix, "add_special_tokens": False},
                           headers=headers, timeout=request.timeout_seconds, provider="chat")
    if isinstance(encoded, ModelFailure):
        return encoded
    return inputs + encoded
