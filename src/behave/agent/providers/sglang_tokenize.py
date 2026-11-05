"""Opt-in CPU isolation for SGLang's non-generating tokenization endpoint.

Reuse the installed SGLang tokenizer, template and request handler in spawned
CPU processes. Both encoding and response serialization stay off the inference
HTTP event loop. No output is shortened and no token count is estimated.
The native training service owns these children in its existing PID lifetime.
"""

import asyncio
import multiprocessing
import os
import pickle
from concurrent.futures import ProcessPoolExecutor
from threading import Lock
from types import SimpleNamespace

PLUGIN = "behave_tokenize_workers"
WORKERS_ENV = "BEHAVE_TOKENIZE_WORKERS"
_handler = None


def _initialize(snapshot):
    # Set this before unpickling native configuration/tokenizer classes: their
    # module imports can inspect CUDA. These are CPU workers, not GPU replicas.
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    manager, template_manager = pickle.loads(snapshot)
    from .sglang_tools import register_qwen_tools
    from sglang.srt.entrypoints.openai.serving_tokenize import OpenAIServingTokenize

    global _handler
    register_qwen_tools()
    _handler = OpenAIServingTokenize(manager, template_manager)


def _encode(request, headers):
    from sglang.srt.entrypoints.openai.serving_base import OpenAIServingBase
    from starlette.responses import Response

    # Call the upstream base, not the opt-in dispatch override in the parent.
    result = asyncio.run(OpenAIServingBase.handle_request(
        _handler, request, SimpleNamespace(headers=headers)))
    if isinstance(result, Response):
        return result.status_code, result.body, dict(result.headers)
    # Returning an already serialized response also avoids FastAPI walking a
    # potentially enormous integer list again on its main event-loop thread.
    return 200, result.model_dump_json().encode("utf-8"), {"content-type": "application/json"}


class TokenizeWorkers:
    def __init__(self, handler, *, workers):
        if type(workers) is not int or workers < 1:
            raise ValueError("tokenize_workers must be a positive integer")
        manager = handler.tokenizer_manager
        # Do not transfer the live TokenizerManager: it owns GPU IPC sockets
        # and asynchronous state. These are the CPU-only inputs of the same
        # upstream tokenize/chat-template handler.
        cpu = SimpleNamespace(**{name: getattr(manager, name) for name in (
            "server_args", "tokenizer", "model_config", "request_logger")})
        template = handler.chat_serving.template_manager if handler.chat_serving is not None else None
        self.snapshot, self.workers = (cpu, template), workers
        self.pool = None
        self._pool_lock = Lock()

    def _submit(self, request, headers):
        with self._pool_lock:
            if self.pool is None:
                # Pickle in this submission thread, not the HTTP event loop.
                # Spawn receives bytes, so CUDA can be hidden before native
                # classes are imported/unpickled in the new interpreter.
                snapshot = pickle.dumps(self.snapshot)
                self.pool = ProcessPoolExecutor(max_workers=self.workers,
                    mp_context=multiprocessing.get_context("spawn"),
                    initializer=_initialize, initargs=(snapshot,))
                self.snapshot = None
        return self.pool.submit(_encode, request, headers)

    async def encode(self, request, headers):
        # ProcessPool.submit may synchronously spawn a worker and block while
        # sending its tokenizer/configuration through the startup pipe. Move
        # submission too, not only the subsequent encode, off the HTTP loop.
        future = await asyncio.to_thread(self._submit, request, headers)
        return await asyncio.wrap_future(future)

    def close(self):
        if self.pool is not None:
            self.pool.shutdown(wait=True, cancel_futures=True)


async def _dispatch(original, self, request, raw_request):
    from starlette.responses import Response

    if not hasattr(self, "_behave_tokenize_workers"):
        self._behave_tokenize_workers = TokenizeWorkers(
            self, workers=int(os.environ[WORKERS_ENV]))
    code, body, headers = await self._behave_tokenize_workers.encode(request, dict(raw_request.headers))
    return Response(body, status_code=code, headers=headers)


def register_tokenize_workers():
    if PLUGIN not in os.environ.get("SGLANG_PLUGINS", "").split(","):
        return
    if int(os.environ[WORKERS_ENV]) < 1:
        raise ValueError("tokenize_workers must be a positive integer")
    from sglang.srt.plugins import HookRegistry
    from sglang.srt.plugins.hook_registry import HookType

    HookRegistry.register(
        "sglang.srt.entrypoints.openai.serving_tokenize.OpenAIServingTokenize.handle_request",
        _dispatch, HookType.AROUND)
