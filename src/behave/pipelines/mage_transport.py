"""MAGE transport types and tool bridge, not its prompts or planning logic."""

import json
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from math import ceil

from pydantic import BaseModel

from behave.agent.attempt import AttemptInterrupted  # noqa: F401 -- imported by the patched MAGE agent
from behave.sim import api

_TOOLS = ContextVar("mage_tools")


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass
class ChatMessage:
    content: str
    role: MessageRole
    additional_kwargs: dict = field(default_factory=dict)


@dataclass
class ChatResponse:
    message: ChatMessage


@dataclass
class ModelBinding:
    calls: object
    temperature: float
    top_p: float
    parallel_requests: int = 10  # Upstream TokenCounter batching default.


class TokenCounter:
    """Keep MAGE's role-call ABI; actual provider usage owns accounting."""

    def __init__(self, llm):
        self.llm, self.cur_tag = llm, ""

    def reset(self):
        self.cur_tag = ""

    def set_cur_tag(self, tag):
        self.cur_tag = tag

    def count_chat(self, messages, llm=None):
        binding = llm or self.llm
        payload = tuple({"role": message.role.value, "content": message.content,
                         **message.additional_kwargs} for message in messages)
        reply = binding.calls.generate(self.cur_tag, payload,
                                       temperature=binding.temperature, top_p=binding.top_p)
        native = reply.message()
        return ChatResponse(ChatMessage(reply.content, MessageRole.ASSISTANT,
                            {"calls": native["calls"], "continuation": native["continuation"]})), dict(reply.usage)

    def count_chat_batch(self, messages_list, llm=None):
        binding = llm or self.llm
        with ThreadPoolExecutor(max_workers=binding.parallel_requests) as pool:
            return list(pool.map(lambda messages: self.count_chat(messages, binding), messages_list))

    def log_token_stats(self):
        # The parent report includes every actual dispatch, including retries.
        return self.llm.calls.accounting()


class TokenCounterCached(TokenCounter):
    @staticmethod
    def is_cache_enabled(llm):
        # Provider-specific caching is owned by the shared model transport.
        return False


def add_lineno(file_content: str) -> str:
    # Identical to upstream mage.utils.add_lineno, without provider SDK imports.
    return "".join(f"{i + 1}: {line}\n" for i, line in enumerate(file_content.split("\n")))


class CommandResult(BaseModel):
    stdout: str
    stderr: str


def run_bash_command(cmd: str, timeout: float | None = None):
    sandbox, workspace = _TOOLS.get()
    command = cmd.replace(str(workspace), "/work")
    options = {} if timeout is None else {"timeout_s": ceil(timeout)}
    result, stdout, stderr = sandbox.execute(
        workspace, api.ToolCommand("mage", ("/tools/bash", "-c", command), **options))
    row, = result.commands
    if row["timed_out"]:
        return False, json.dumps({"stdout": "", "stderr": f"Timeout {timeout}s reached."}, indent=4)
    return row["returncode"] == 0, json.dumps({"stdout": stdout, "stderr": stderr}, indent=4)
