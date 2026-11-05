"""Sandbox-side Bash client. Only declared file bytes cross to the Sim service."""

import argparse
import base64
import json
import os
import time
import uuid
from pathlib import Path


def encode(path):
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


def request(value):
    selections = {name: value[name] for name in ("behavior", "reference_behavior", "rtl") if name in value}
    paths = dict.fromkeys(path for selection in selections.values() for path in selection["files"])
    payload = {
        **selections,
        "files": [{"path": path, "data": encode(path)} for path in paths],
        "contract": encode(value["contract"]),
        "run_policy": None if value.get("run_policy") is None else encode(value["run_policy"]),
    }
    if value.get("replay_stimuli") is not None:
        payload["replay_stimuli"] = encode(value["replay_stimuli"])
    if value.get("spec") is not None:
        payload["spec"] = encode(value["spec"])
    return payload


def exchange(endpoint, payload, poll_seconds):
    # The development sandbox prohibits sockets. A private mailbox leaves that
    # boundary intact; the shell/Sim runtime still owns all execution deadlines.
    root, key = Path(endpoint), uuid.uuid4().hex
    temporary = root / (key + ".pending")
    temporary.write_text(json.dumps(payload))
    temporary.rename(root / (key + ".request.json"))
    response = root / (key + ".response.json")
    while not response.exists():
        time.sleep(poll_seconds)
    return json.loads(response.read_text())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path, help="JSON with behavior selection and contract/run_policy paths")
    args = parser.parse_args(argv)
    result = exchange("/sim-check", request(json.loads(args.request.read_text())),
                      float(os.environ["BEHAVE_SIM_CHECK_POLL_SECONDS"]))
    for file in result["files"]:
        path = Path(file["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(file["data"], validate=True))
    reply = result["reply"]
    print(json.dumps(reply, indent=2))
    return 0 if reply.get("matched") is True else 1 if reply.get("matched") is False else 2


if __name__ == "__main__":
    raise SystemExit(main())
