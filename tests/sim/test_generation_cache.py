"""Semantic identity, durability and concurrency tests for generation contexts."""

import concurrent.futures
import json
import multiprocessing
import os
import threading
import time
from types import SimpleNamespace

import pytest

from behave.sim.core.record_value import canonical_json
from behave.sim.generation_cache import (
    GenerationCacheBinding,
    GenerationCacheEvent,
    GenerationCacheKey,
    GenerationCacheKind,
    GenerationContextCache,
    GenerationPayloadKind,
)


def _binding_values():
    return {
        "source": "reference:one",
        "target": "artifact:one",
        "task_contract": "contract:one",
        "generation_spec": "generation-spec:one",
        "catalogue": "catalogue:one",
        "goal_policy": "goal-policy:one",
        "representation": "representation:one",
        "encoding": "encoding:one",
        "environment": "environment:one",
        "provider": "behavior/reference-to-artifact",
        "provider_policy": "behavior-local-guidance/1",
        "frontend": "behavior",
        "frontend_version": "behavior/4",
        "backend": "z3-exact-local",
        "backend_version": "behavior-generation/1",
        "tool": "z3",
        "tool_version": "4.16.0",
    }


def _binding(**changes):
    values = _binding_values()
    values.update(changes)
    return GenerationCacheBinding(**values)


def _probe_key(**changes):
    values = {
        "binding": _binding(),
        "seed_bytes": b'{"queues":{"in":[[1]]},"slots":[0]}',
        "trace_identity": "audited-trace:one",
        "request_bytes": b'{"occurrence":"step:1","mutable_fields":[[0,0]]}',
    }
    values.update(changes)
    return GenerationCacheKey.probe(**values)


@pytest.mark.parametrize("field", tuple(_binding_values()))
def test_every_binding_component_invalidates_the_semantic_cache_key(field):
    baseline = _binding()
    changed = _binding(**{field: _binding_values()[field] + ":changed"})

    assert changed.identity != baseline.identity
    assert GenerationCacheKey.layout(changed).identity \
        != GenerationCacheKey.layout(baseline).identity
    if field in {"frontend", "frontend_version", "backend", "backend_version",
                 "tool", "tool_version"}:
        assert changed.semantic_instance == baseline.semantic_instance
    else:
        assert changed.semantic_instance != baseline.semantic_instance


@pytest.mark.parametrize("previous_version", [f'behavior/{version}' for version in range(168, 182)])
def test_behavior_layout_binding_invalidates_previous_frontends(monkeypatch, previous_version):
    from behave.sim.behavior import instance
    from behave.sim.engines.behavior.generation import _generation_cache_binding

    current_version = instance.FRONTEND
    assert current_version == 'behavior/182'
    task = SimpleNamespace(contract=SimpleNamespace(digest='same-contract'))
    spec = SimpleNamespace(max_body_steps=3)
    current = _generation_cache_binding(task, 'same-target', spec, 'same-source')
    monkeypatch.setattr(instance, 'FRONTEND', previous_version)
    previous = _generation_cache_binding(task, 'same-target', spec, 'same-source')
    assert current.frontend_version == current_version
    assert previous.frontend_version == previous_version
    assert GenerationCacheKey.layout(current).identity != GenerationCacheKey.layout(previous).identity


def test_binding_and_keys_are_closed_versioned_exact_images():
    binding = _binding()
    layout = GenerationCacheKey.layout(binding)
    select = GenerationCacheKey.select(
        binding, seed_bytes=b"seed\x00bytes", trace_identity="trace:one")
    probe = GenerationCacheKey.probe(
        binding, seed_bytes=b"seed\x00bytes", trace_identity="trace:one",
        request_bytes=b"request\xffbytes")

    assert GenerationCacheBinding.from_dict(binding.as_dict()).as_dict() \
        == binding.as_dict()
    for key in (layout, select, probe):
        assert GenerationCacheKey.from_dict(key.as_dict()).as_dict() == key.as_dict()
    assert len({layout.identity, select.identity, probe.identity}) == 3
    with pytest.raises(TypeError):
        GenerationCacheBinding(**_binding_values(), timeout_ms=1)
    with pytest.raises(TypeError):
        GenerationCacheBinding(**_binding_values(), workers=8)
    with pytest.raises(TypeError):
        GenerationCacheBinding(**_binding_values(), probe_budget=4)
    with pytest.raises(ValueError, match="carries no seed-local"):
        GenerationCacheKey(
            binding, GenerationCacheKind.LAYOUT, seed_bytes=b"not-layout")
    with pytest.raises(TypeError, match="request bytes"):
        GenerationCacheKey.probe(
            binding, seed_bytes=b"seed", trace_identity="trace",
            request_bytes=bytearray(b"not-exact"))


def test_seed_trace_and_request_exact_bytes_independently_invalidate_keys():
    baseline = _probe_key()
    assert _probe_key(seed_bytes=baseline.seed_bytes + b"\x00").identity \
        != baseline.identity
    assert _probe_key(trace_identity=baseline.trace_identity + ":changed").identity \
        != baseline.identity
    assert _probe_key(request_bytes=baseline.request_bytes + b"\x00").identity \
        != baseline.identity
    # The complete bytes, including NUL and non-UTF8 data, survive the public image.
    restored = GenerationCacheKey.from_dict(baseline.as_dict())
    assert restored.seed_bytes == baseline.seed_bytes
    assert restored.request_bytes == baseline.request_bytes


def test_json_and_byte_payloads_are_detached_and_schema_separated(tmp_path):
    cache = GenerationContextCache(tmp_path / "cache")
    key = GenerationCacheKey.layout(_binding())
    builds = []

    cold = cache.get_or_build(
        key, payload_schema="behavior-layout/1",
        payload_kind=GenerationPayloadKind.JSON,
        builder=lambda: builds.append("json") or {"rows": [1, 2]})
    first_view = cold.payload_json
    first_view["rows"].append(99)
    warm = cache.get_or_build(
        key, payload_schema="behavior-layout/1",
        payload_kind=GenerationPayloadKind.JSON,
        builder=lambda: pytest.fail("a valid warm context rebuilt"))
    binary = cache.get_or_build(
        key, payload_schema="behavior-layout-wire/1",
        payload_kind=GenerationPayloadKind.BYTES,
        builder=lambda: builds.append("bytes") or b"\x00layout\xff")

    assert not cold.cache_hit and warm.cache_hit and not binary.cache_hit
    assert cold.payload_json == warm.payload_json == {"rows": [1, 2]}
    assert binary.payload_bytes == b"\x00layout\xff"
    assert builds == ["json", "bytes"]
    assert cold.record_identity != binary.record_identity


def test_corrupt_or_inconsistent_records_are_misses_and_are_repaired(tmp_path):
    cache = GenerationContextCache(tmp_path / "cache")
    key = _probe_key()
    schema = "behavior-probe-context/1"
    kind = GenerationPayloadKind.JSON
    first = cache.get_or_build(
        key, payload_schema=schema, payload_kind=kind,
        builder=lambda: {"formula": "first"})
    path = cache._record_path(first.record_identity)
    image = json.loads(path.read_bytes())
    image["payload_digest"] = "0" * 64
    path.write_bytes(canonical_json(image).encode())

    builds = []
    repaired = cache.get_or_build(
        key, payload_schema=schema, payload_kind=kind,
        builder=lambda: builds.append("repair") or {"formula": "second"})
    warm = cache.load(key, payload_schema=schema, payload_kind=kind)

    assert not repaired.cache_hit
    assert repaired.payload_json == warm.payload_json == {"formula": "second"}
    assert builds == ["repair"]


def test_entry_reads_do_not_follow_a_replaced_symlink(tmp_path):
    cache = GenerationContextCache(tmp_path / "cache")
    key = GenerationCacheKey.layout(_binding())
    got = cache.get_or_build(
        key, payload_schema="layout/1", payload_kind=GenerationPayloadKind.BYTES,
        builder=lambda: b"trusted context")
    path = cache._record_path(got.record_identity)
    target = tmp_path / "attacker-controlled-entry"
    target.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(target)

    assert cache.load(
        key, payload_schema="layout/1",
        payload_kind=GenerationPayloadKind.BYTES) is None


def test_eviction_does_not_follow_a_symlinked_prefix(tmp_path):
    cache = GenerationContextCache(
        tmp_path / "cache", max_entries=1, max_bytes=1 << 20)
    external = tmp_path / "outside-cache"
    external.mkdir()
    victim = external / "victim.json"
    victim.write_text("must survive")
    prefix = next(
        f"{value:02x}" for value in range(256)
        if not (cache._entries / f"{value:02x}").exists())
    (cache._entries / prefix).symlink_to(external, target_is_directory=True)

    for index in range(2):
        cache.get_or_build(
            _probe_key(seed_bytes=f"evict-{index}".encode()),
            payload_schema="eviction/1",
            payload_kind=GenerationPayloadKind.BYTES,
            builder=lambda index=index: f"context-{index}".encode())

    assert victim.read_text() == "must survive"


def test_cache_event_rejects_string_subclasses():
    class Text(str):
        pass

    with pytest.raises(ValueError, match="known disposition"):
        GenerationCacheEvent(
            GenerationCacheKind.LAYOUT, Text("hit"), "key")
    with pytest.raises(TypeError, match="detail is exact text"):
        GenerationCacheEvent(
            GenerationCacheKind.LAYOUT, "hit", "key", detail=Text("detail"))


def test_same_key_is_single_flight_across_threads(tmp_path):
    cache = GenerationContextCache(tmp_path / "cache")
    key = _probe_key()
    calls = []
    entered = threading.Event()

    def build():
        calls.append("build")
        entered.set()
        time.sleep(0.08)
        return b"context"

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(
            cache.get_or_build, key, payload_schema="probe-wire/1",
            payload_kind=GenerationPayloadKind.BYTES, builder=build)
            for _index in range(8)]
        assert entered.wait(2)
        got = [future.result(timeout=5) for future in futures]

    assert calls == ["build"]
    assert sum(not row.cache_hit for row in got) == 1
    assert [row.payload_bytes for row in got] == [b"context"] * 8


def _process_cache_lookup(root, key_image, start, calls, results):
    cache = GenerationContextCache(root)
    key = GenerationCacheKey.from_dict(key_image)
    start.wait()

    def build():
        with calls.get_lock():
            calls.value += 1
        time.sleep(0.1)
        return b"process-context"

    got = cache.get_or_build(
        key, payload_schema="process-wire/1",
        payload_kind=GenerationPayloadKind.BYTES, builder=build)
    results.put((got.cache_hit, got.payload_bytes))


def test_same_key_is_single_flight_across_processes(tmp_path):
    context = multiprocessing.get_context("fork")
    root = tmp_path / "cache"
    key = _probe_key()
    start = context.Event()
    calls = context.Value("i", 0)
    results = context.Queue()
    workers = [context.Process(
        target=_process_cache_lookup,
        args=(root, key.as_dict(), start, calls, results)) for _index in range(2)]
    for worker in workers:
        worker.start()
    start.set()
    for worker in workers:
        worker.join(10)
    assert [worker.exitcode for worker in workers] == [0, 0]
    got = [results.get(timeout=2) for _worker in workers]
    assert calls.value == 1
    assert sorted(hit for hit, _payload in got) == [False, True]
    assert [payload for _hit, payload in got] == [b"process-context"] * 2


def test_different_keys_build_in_parallel(tmp_path):
    cache = GenerationContextCache(tmp_path / "cache")
    left = _probe_key(seed_bytes=b"left")
    right = _probe_key(seed_bytes=b"right")
    # A fixed lock stripe set is bounded. Pick two semantic keys that do not share the
    # rare conservative stripe collision so this test measures absence of a global lock.
    suffix = 0
    while cache._lock_path(left) == cache._lock_path(right):
        suffix += 1
        right = _probe_key(seed_bytes=f"right-{suffix}".encode())
    both = threading.Event()
    gate = threading.Lock()
    active = 0
    maximum = 0

    def build(value):
        nonlocal active, maximum
        with gate:
            active += 1
            maximum = max(maximum, active)
            if active == 2:
                both.set()
        assert both.wait(2)
        time.sleep(0.02)
        with gate:
            active -= 1
        return value

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                cache.get_or_build, key, payload_schema="parallel/1",
                payload_kind=GenerationPayloadKind.BYTES,
                builder=lambda value=value: build(value))
            for key, value in ((left, b"left"), (right, b"right"))]
        got = [future.result(timeout=5).payload_bytes for future in futures]
    assert got == [b"left", b"right"]
    assert maximum == 2


def test_failed_or_invalid_builder_is_never_published_and_can_retry(tmp_path):
    cache = GenerationContextCache(tmp_path / "cache")
    key = GenerationCacheKey.layout(_binding())
    attempts = []

    def build():
        attempts.append("build")
        if len(attempts) == 1:
            raise RuntimeError("failed context")
        return {"ok": True}

    with pytest.raises(RuntimeError, match="failed context"):
        cache.get_or_build(
            key, payload_schema="layout/1", payload_kind=GenerationPayloadKind.JSON,
            builder=build)
    with pytest.raises(TypeError, match="outside the finite record carrier"):
        cache.get_or_build(
            key, payload_schema="layout/1", payload_kind=GenerationPayloadKind.JSON,
            builder=lambda: object())
    cold = cache.get_or_build(
        key, payload_schema="layout/1", payload_kind=GenerationPayloadKind.JSON,
        builder=build)
    warm = cache.get_or_build(
        key, payload_schema="layout/1", payload_kind=GenerationPayloadKind.JSON,
        builder=lambda: pytest.fail("failed build was cached as success"))

    assert attempts == ["build", "build"]
    assert not cold.cache_hit and warm.cache_hit
    assert warm.payload_json == {"ok": True}


def test_atomic_publication_failure_does_not_clobber_existing_bytes(
        monkeypatch, tmp_path):
    cache = GenerationContextCache(tmp_path / "cache")
    key = GenerationCacheKey.layout(_binding())
    got = cache.get_or_build(
        key, payload_schema="layout/1", payload_kind=GenerationPayloadKind.BYTES,
        builder=lambda: b"old")
    path = cache._record_path(got.record_identity)
    before = path.read_bytes()

    monkeypatch.setattr(
        os, "replace", lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("publication interrupted")))
    with pytest.raises(OSError, match="publication interrupted"):
        cache._publish(path, b"new incomplete record")

    assert path.read_bytes() == before
    assert not list(path.parent.glob("*.partial"))


def test_durable_store_evicts_the_oldest_unprotected_entry(tmp_path):
    cache = GenerationContextCache(
        tmp_path / "cache", max_entries=2, max_bytes=1 << 20)
    keys = [_probe_key(seed_bytes=f"seed-{index}".encode()) for index in range(3)]
    for index in range(2):
        cache.get_or_build(
            keys[index], payload_schema="bounded/1",
            payload_kind=GenerationPayloadKind.BYTES,
            builder=lambda index=index: f"value-{index}".encode())
        time.sleep(0.01)
    # Refresh key zero, making key one the eviction victim.
    assert cache.load(
        keys[0], payload_schema="bounded/1",
        payload_kind=GenerationPayloadKind.BYTES).cache_hit
    time.sleep(0.01)
    cache.get_or_build(
        keys[2], payload_schema="bounded/1",
        payload_kind=GenerationPayloadKind.BYTES, builder=lambda: b"value-2")

    assert cache.load(
        keys[0], payload_schema="bounded/1",
        payload_kind=GenerationPayloadKind.BYTES) is not None
    assert cache.load(
        keys[1], payload_schema="bounded/1",
        payload_kind=GenerationPayloadKind.BYTES) is None
    assert cache.load(
        keys[2], payload_schema="bounded/1",
        payload_kind=GenerationPayloadKind.BYTES) is not None


def test_oversized_context_is_usable_but_not_retained(tmp_path):
    cache = GenerationContextCache(tmp_path / "cache", max_bytes=64)
    key = GenerationCacheKey.layout(_binding())
    builds = []

    for _attempt in range(2):
        got = cache.get_or_build(
            key, payload_schema="large/1", payload_kind=GenerationPayloadKind.BYTES,
            builder=lambda: builds.append("build") or b"x" * 1000)
        assert not got.cache_hit
        assert got.payload_bytes == b"x" * 1000
    assert builds == ["build", "build"]
    assert cache.load(
        key, payload_schema="large/1",
        payload_kind=GenerationPayloadKind.BYTES) is None
