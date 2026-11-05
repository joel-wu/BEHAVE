"""Trusted Bash fixtures for Syn orchestration tests.

These scripts use the same command schema as production. The lightweight local
runner is only for scripted unit tests, not a sandbox. Process/isolation tests
execute these commands through the real shared development shell instead.
"""

import ast
import shlex
import subprocess

from test_attempts import call

from behave.agent.tools import Tool, ToolResult, object_schema
from behave.core.records import RecordError


def shell_call(operation, arguments, key="call"):
    if operation == "write":
        script = ("from pathlib import Path; p=Path(" + repr(arguments["path"]) + "); "
                  "p.parent.mkdir(parents=True, exist_ok=True); p.write_text(" + repr(arguments["content"]) + ")")
        command = "python3 -c " + shlex.quote(script)
    elif operation == "read":
        command = "cat -- " + shlex.quote(arguments["path"])
    elif operation == "list":
        command = "python3 -c " + shlex.quote(
            "import json; from pathlib import Path; "
            "print(json.dumps({'files': [{'path': str(p)} for p in sorted(Path('.').rglob('*')) if p.is_file()]}))")
    else:
        raise ValueError(operation)
    return call("run_command", {"command": command}, key)


def fixture_shell():
    def execute(workspace, arguments):
        with workspace.materialize() as root:
            result = subprocess.run(["/bin/bash", "--noprofile", "--norc", "-c", arguments["command"]],
                                    cwd=root, capture_output=True, text=True, timeout=30)
            try:
                workspace.import_directory(root)
            except RecordError as error:
                return ToolResult({"status": "completed", "exit_code": 1,
                                   "stdout": result.stdout, "stderr": error.code})
        return ToolResult({"status": "completed", "exit_code": result.returncode,
                           "stdout": result.stdout, "stderr": result.stderr})
    return Tool("run_command", "Trusted scripted Bash fixture", object_schema({"command": {"type": "string"}}),
                execute, "syn-fixture-shell/1")


def written_content(reply):
    """Extract the literal fixture text without executing the write command."""
    script = shlex.split(reply.calls[0].arguments["command"])[-1]
    return ast.literal_eval(ast.parse(script).body[-1].value.args[0])
