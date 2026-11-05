"""Stable Syn model identity over the trainer's restartable local endpoint.

Only the trusted training coordinator writes the connection. Role journals bind
the fixed checkpoint/profile, not transient serving ports. Wire conversion,
context handling and response decoding remain the shared chat implementation.
"""

import sys
from dataclasses import replace
from pathlib import Path

from behave.agent.model import ModelRequest
from behave.agent.process_model import ModelCommand, encode_model_outcome
from behave.agent.providers.chat_worker import exchange
from behave.agent.providers.local_chat import LocalChatEndpoint
from behave.core.records import canonical_json, parse_json, thaw_json


def model_command(connection, profile, checkpoint, *, generation_parameters=None):
    # The tiny stdlib bootstrap is stable even when the training control venv
    # is reconstructed at a different local path after a node failure.
    worker = ("import sys; sys.path.insert(0,sys.argv.pop(1)); "
              "from behave.improve.training_connection import main; main()")
    bootstrap = ("import json,os,sys; c=json.load(open(sys.argv[1])); "
                 "os.execv(c['control_python'], [c['control_python'],'-I','-B','-c',"
                 + repr(worker) + ",c['control_package'],*sys.argv[1:]])")
    approved = {"profile": profile, "checkpoint": checkpoint}
    if generation_parameters is not None:
        approved["generation_parameters"] = generation_parameters
    return ModelCommand(("/usr/bin/python3", "-I", "-B", "-c", bootstrap, str(connection),
        canonical_json(approved).decode()),
        "behave.training-owned-chat/2" if generation_parameters is not None else
        "behave.training-owned-chat/1", network="host")


def connected_exchange(connection, approved, request):
    if connection["checkpoint"] != approved["checkpoint"]:
        raise ValueError("Syn connection belongs to another training checkpoint")
    endpoint = LocalChatEndpoint.from_dict(connection["endpoint"])
    profile = approved["profile"]
    if (endpoint.model != profile["model"] or endpoint.runtime_revision != profile["runtime_revision"]
            or thaw_json(endpoint.parameters) != profile["parameters"]):
        raise ValueError("Syn connection changed its approved sampling/model profile")
    # A separate, command-bound generation profile is permitted only after
    # authenticating the original checkpoint and trainer connection. It never
    # mutates the trainer endpoint or the other role commands.
    if "generation_parameters" in approved:
        endpoint = replace(endpoint, parameters=approved["generation_parameters"])
    # The outer request/response is still bound to the stable command. Rebind
    # only the internal HTTP transport's provider identity after the above check.
    inner = replace(request, model_policy={**thaw_json(request.model_policy),
                                         "provider": endpoint.identity})
    return exchange(endpoint, inner)


def main():
    connection = parse_json(Path(sys.argv[1]).read_bytes())
    approved = parse_json(sys.argv[2].encode())
    request = ModelRequest.from_dict(parse_json(sys.stdin.buffer.read()))
    result = connected_exchange(connection, approved, request)
    sys.stdout.buffer.write(encode_model_outcome(request, result))
