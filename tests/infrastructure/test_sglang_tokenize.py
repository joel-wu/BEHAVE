"""The same native tokenizer/response, without blocking other requests."""

import asyncio
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from behave.agent.providers.sglang_tokenize import TokenizeWorkers, register_tokenize_workers


class FixtureTokenizer:
    model_max_length = 131072

    def __init__(self, directory):
        self.directory = str(directory)

    def encode(self, text, *, add_special_tokens):
        assert os.environ.get("CUDA_VISIBLE_DEVICES") == ""
        if text == "slow":
            directory = Path(self.directory)
            (directory / "started").write_text(str(os.getpid()))
            while not (directory / "release").exists():
                time.sleep(.01)
        if text == "error":
            raise ValueError("fixture tokenization error")
        return ([1] if add_special_tokens else []) + [ord(c) for c in text]


def native_handler(tokenizer, template=None, server_args=None, model_config=None):
    pytest.importorskip("sglang")
    from sglang.srt.entrypoints.openai.serving_tokenize import OpenAIServingTokenize

    return OpenAIServingTokenize(SimpleNamespace(tokenizer=tokenizer,
        server_args=server_args or SimpleNamespace(), model_config=model_config,
        request_logger=SimpleNamespace(log_requests=False)), template)


def test_plugin_is_opt_in(monkeypatch):
    monkeypatch.delenv("SGLANG_PLUGINS", raising=False)
    register_tokenize_workers()


def test_slow_worker_start_does_not_block_event_loop():
    from concurrent.futures import Future
    from threading import Event, Lock

    started, release = Event(), Event()

    class StartingPool:
        def submit(self, *args):
            started.set()
            assert release.wait(5), "event loop was blocked by synchronous process startup"
            future = Future()
            future.set_result((200, b"{}", {}))
            return future

    workers = object.__new__(TokenizeWorkers)
    workers.pool = StartingPool()
    workers._pool_lock = Lock()

    async def probe():
        pending = asyncio.create_task(workers.encode(None, {}))
        try:
            while not started.is_set():
                await asyncio.sleep(0)
        finally:
            release.set()
        assert await pending == (200, b"{}", {})

    asyncio.run(probe())


def test_slow_tokenizer_does_not_hold_other_requests_or_event_loop(tmp_path, monkeypatch):
    handler = native_handler(FixtureTokenizer(tmp_path))
    from sglang.srt.entrypoints.openai.protocol import TokenizeRequest

    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "parent-devices-must-not-change")
    workers = TokenizeWorkers(handler, workers=2)

    async def probe():
        slow = asyncio.create_task(workers.encode(TokenizeRequest(prompt="slow"), {}))
        try:
            while not (tmp_path / "started").exists():
                await asyncio.sleep(.01)
            assert int((tmp_path / "started").read_text()) != os.getpid()
            code, short, _ = await workers.encode(TokenizeRequest(prompt="hello", add_special_tokens=False), {})
            assert code == 200
            assert json.loads(short)["tokens"] == list(map(ord, "hello"))
            assert not slow.done()
            code, error, _ = await workers.encode(TokenizeRequest(prompt="error"), {})
            assert code == 400
            assert "fixture tokenization error" in json.loads(error)["message"]
        finally:
            (tmp_path / "release").touch()
            await slow

    try:
        asyncio.run(asyncio.wait_for(probe(), timeout=180))
    finally:
        (tmp_path / "release").touch()
        workers.close()
    assert os.environ["CUDA_VISIBLE_DEVICES"] == "parent-devices-must-not-change"


def test_plugin_hook_returns_serialized_response(tmp_path, monkeypatch):
    handler = native_handler(FixtureTokenizer(tmp_path))
    from sglang.srt.entrypoints.openai.protocol import TokenizeRequest
    from sglang.srt.plugins import HookRegistry
    from starlette.responses import Response

    monkeypatch.setenv("SGLANG_PLUGINS", "behave_tokenize_workers")
    monkeypatch.setenv("BEHAVE_TOKENIZE_WORKERS", "2")
    register_tokenize_workers()
    HookRegistry.apply_hooks()
    try:
        result = asyncio.run(handler.handle_request(
            TokenizeRequest(prompt="hook", add_special_tokens=False), SimpleNamespace(headers={})))
        assert isinstance(result, Response)
        assert json.loads(result.body)["tokens"] == list(map(ord, "hook"))
    finally:
        handler._behave_tokenize_workers.close()
