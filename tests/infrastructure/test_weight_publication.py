"""Committed lineage and local staging; fake tensors, no GPU-acceptance claim."""

import errno
from dataclasses import replace
from io import BytesIO
from pathlib import Path

import pytest
from test_serving import (
    lifecycle,  # noqa: F401 -- shared mocked service lifecycle
    spec,
)
from test_training_checkpoints import fixture as selection_fixture  # noqa: F401

from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.serving.model_files import inspect_model_files
from behave.train.checkpoint_storage import WeightPublication, load_weights, publish_weights, stage_weights
from behave.train.checkpoints import TrainingLedger, copy_state_file, store_state_file


@pytest.fixture(autouse=True)
def staging_root(tmp_path, monkeypatch):
    from behave.train import checkpoint_storage as module

    root = tmp_path / "weight-staging"
    root.mkdir()
    monkeypatch.setattr(module, "scratch_root", lambda: root)
    return root


def with_weights(source, native, *, indexed=False):
    payloads = {"config.json": b'{"model_type":"fixture"}', "tokenizer.json": b'{"fixture":true}',
                "tokenizer_config.json": b'{"chat_template":"fixture template"}',
                "extra/chat_template.jinja": b"fixture template", "model.safetensors": b"fake tensors:0123456789"}
    if indexed:
        payloads["model.safetensors.index.json"] = canonical_json({"weight_map": {
            "linear.weight": "model.safetensors", "linear.bias": "model.safetensors"}})
    files = tuple(store_state_file(BytesIO(data), source, component="serving", path=name, chunk_bytes=7)
                  for name, data in payloads.items())
    return replace(native, files=(*native.files, *files)), payloads


@pytest.fixture
def trained(tmp_path, selection_fixture):  # noqa: F811
    source, contract, native, request, _ = selection_fixture
    contract = replace(contract, components=(*contract.components, "serving"))
    state, payloads = with_weights(source, native)
    run = JournalDirectory(tmp_path / "training")
    with TrainingLedger(run, contract) as ledger:
        before = ledger.initialize(state, source)
        ticket = ledger.begin(request(before.reference), source)
        # An actual second commit, with a distinct native-policy identity.
        state = replace(state, policy=BlobRef.of(b"trained-policy"), cursor={"step": 1})
        committed = ledger.commit(ticket.reference, state, source)
        head, _ = ledger.seal()
    return run.objects, head, contract, committed, payloads, before.reference


def publish(trained, objects):
    reader, head, contract, _, _, _ = trained
    return publish_weights(reader, head, contract=contract, component="serving", objects=objects)


def load(trained, reader, ref, **changes):
    _, head, contract, _, _, _ = trained
    args = {"checkpoint": head, "contract": contract, "component": "serving"}
    return load_weights(reader, ref, **(args | changes))


def stage(trained, reader, ref, **changes):
    _, head, contract, _, _, _ = trained
    args = {"checkpoint": head, "contract": contract, "component": "serving", "spec": spec()}
    return stage_weights(reader, ref, **(args | changes))


def test_publication_cold_copy_preserves_exact_committed_lineage(trained, tmp_path, monkeypatch, staging_root):
    from behave.train import checkpoint_storage as module

    load_chain = module.load_chain

    def history(reader, head):
        return load_chain(reader, head)

    monkeypatch.setattr(module, "load_chain", history)
    objects = DirectoryStore(tmp_path / "published")
    ref = publish(trained, objects)
    assert publish(trained, objects) == ref  # Neither optimizer nor converter dispatch.
    publication, files = load(trained, objects, ref)
    _, head, contract, checkpoint, payloads, _ = trained
    assert publication == WeightPublication(head, BlobRef.of(contract.to_bytes()), checkpoint.state_ref,
                                             checkpoint.state.policy, "serving")
    assert WeightPublication.from_bytes(publication.to_bytes()) == publication
    assert {file.path for file in files} == set(payloads)
    # Native state is in the private evidence closure, not in inference files.
    assert {file.component for file in files} == {"serving"}
    for file in files:
        out = BytesIO()
        copy_state_file(objects, file, out)
        assert out.getvalue() == payloads[file.path]
    fresh = stage(trained, objects, ref)
    directory = Path(fresh.model_path)
    assert directory.parent == staging_root and directory.stat().st_mode & 0o777 == 0o700
    assert {p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()} == set(payloads)
    for name, data in payloads.items():
        path = directory / name
        assert not path.is_symlink() and path.read_bytes() == data
        assert path.stat().st_mode & 0o777 == 0o444
    assert inspect_model_files(directory) == {"model.safetensors"}
    assert fresh.model_revision == ref.sha256 and fresh.identity != spec().identity
    assert replace(fresh, model_path=spec().model_path, model_revision=spec().model_revision) == spec()
    for runtime, flag in (("sglang", "--model-path"), ("vllm", "--model")):
        command = replace(fresh, runtime=runtime).command()
        assert command[command.index(flag) + 1] == str(directory)
        assert command[command.index("--revision") + 1] == ref.sha256
    # No context manager deletes a tree when a caller/server has an uncertain outcome.
    assert directory.is_dir()


def test_distributed_inference_export_requires_explicit_rank(tmp_path, selection_fixture):  # noqa: F811
    from behave.train.cluster_state import pack_rank_states

    source, contract, native, _, _ = selection_fixture
    contract = replace(contract, components=(*contract.components, "serving"))
    state, payloads = with_weights(source, native)
    run = JournalDirectory(tmp_path / "distributed")
    with TrainingLedger(run, contract) as ledger:
        checkpoint = ledger.initialize(pack_rank_states((state, state)), source)
        head, _ = ledger.seal()
    objects = DirectoryStore(tmp_path / "published")
    with pytest.raises(RecordError, match="select the rank"):
        publish_weights(run.objects, head, contract=contract, component="serving", objects=objects)
    with pytest.raises(RecordError, match="outside"):
        publish_weights(run.objects, head, contract=contract, component="serving", rank=2, objects=objects)
    publication_ref = publish_weights(run.objects, head, contract=contract, component="serving", rank=1, objects=objects)
    publication, files = load_weights(objects, publication_ref, checkpoint=head, contract=contract, component="serving")
    assert publication.rank == 1 and publication.policy == checkpoint.state.policy
    assert parse_json(publication.to_bytes())["schema"] == "behave.weight-publication/2"
    assert {file.path for file in files} == set(payloads)
    assert all(not file.path.startswith("ranks/") for file in files)
    # An independently pinned publication also fixes which rank's complete
    # export is handed to the existing SGLang/vLLM staging path.
    staged = stage_weights(objects, publication_ref, checkpoint=head, contract=contract,
                           component="serving", spec=spec())
    directory = Path(staged.model_path)
    assert (directory / "config.json").read_bytes() == payloads["config.json"]


@pytest.mark.parametrize("kind", ["checkpoint", "contract", "component", "policy", "state"])
def test_old_or_relabelled_weights_cannot_satisfy_another_expected_lineage(trained, tmp_path, kind):
    objects = DirectoryStore(tmp_path / "published")
    ref = publish(trained, objects)
    if kind in {"state", "policy"}:
        value = WeightPublication.from_bytes(objects.read(ref))
        ref = objects.put(replace(value, **{kind: BlobRef.of(b"unrelated")}).to_bytes())
        changes = {}
    else:
        changes = {kind: {"checkpoint": trained[-1], "contract": replace(trained[2], adapter="other/1"),
                          "component": "model"}[kind]}
    with pytest.raises(RecordError, match="publication") as error:
        load(trained, objects, ref, **changes)
    assert error.value.code == "weights-lineage-mismatch"


def test_pending_head_never_publishes_the_previous_committed_policy(tmp_path, selection_fixture):  # noqa: F811
    source, contract, native, request, _ = selection_fixture
    contract = replace(contract, components=(*contract.components, "serving"))
    state, _ = with_weights(source, native)
    objects = DirectoryStore(tmp_path / "published")
    with TrainingLedger(JournalDirectory(tmp_path / "training"), contract) as ledger:
        before = ledger.initialize(state, source)
        ledger.begin(request(before.reference), source)
        head, _ = ledger.seal()
        with pytest.raises(RecordError) as error:
            publish_weights(ledger.run.objects, head, contract=contract, component="serving", objects=objects)
        assert error.value.code == "weights-checkpoint-uncommitted"
    assert not objects.directory.exists()


def test_failed_dependency_publication_has_no_root_and_retry_is_idempotent(trained, tmp_path):
    objects = DirectoryStore(tmp_path / "published")
    expected = WeightPublication(trained[1], BlobRef.of(trained[2].to_bytes()), trained[3].state_ref,
                                 trained[3].state.policy, "serving")
    expected_ref = BlobRef.of(expected.to_bytes())

    class Broken(DirectoryStore):
        def publish(self, reader, refs, **kwargs):
            self.put(reader.read(refs[0]))
            raise OSError("lost dependency write")

    with pytest.raises(OSError):
        publish(trained, Broken(objects.directory))
    assert not (objects.directory / expected_ref.sha256).exists()

    class LostAck(DirectoryStore):
        def put(self, data):
            ref = super().put(data)
            if ref == expected_ref:
                raise OSError("committed, lost acknowledgement")
            return ref

    with pytest.raises(OSError):
        publish(trained, LostAck(objects.directory))
    assert load(trained, objects, expected_ref)[0] == expected
    assert publish(trained, objects) == expected_ref


def test_corrupt_native_state_still_blocks_serving_publication(trained, tmp_path):
    objects = DirectoryStore(tmp_path / "published")
    ref = publish(trained, objects)
    victim = next(f for f in trained[3].state.files if f.component == "optimizer").chunks[0]
    path = objects.directory / victim.sha256
    path.chmod(0o600)
    path.write_bytes(b"corrupt")
    with pytest.raises(RecordError) as error:
        load(trained, objects, ref)
    assert error.value.code == "content-identity-mismatch"


@pytest.mark.parametrize("fault", ["component", "config", "weights", "index", "missing-shard"])
def test_incomplete_inference_component_is_not_published(tmp_path, selection_fixture, fault):  # noqa: F811
    source, contract, native, _, _ = selection_fixture
    contract = replace(contract, components=(*contract.components, "serving"))
    state, _ = with_weights(source, native, indexed=True)
    if fault in {"config", "weights"}:
        name = "config.json" if fault == "config" else "model.safetensors"
        state = replace(state, files=tuple(f for f in state.files if f.path != name))
    elif fault in {"index", "missing-shard"}:
        index = b"bad-json" if fault == "index" else canonical_json({"weight_map": {"x": "absent.safetensors"}})
        file = store_state_file(BytesIO(index), source, component="serving", path="model.safetensors.index.json")
        state = replace(state, files=tuple(f for f in state.files if f.path != file.path) + (file,))
    objects = DirectoryStore(tmp_path / "published")
    with TrainingLedger(JournalDirectory(tmp_path / "training"), contract) as ledger:
        ledger.initialize(state, source)
        head, _ = ledger.seal()
        with pytest.raises(ValueError):
            publish_weights(ledger.run.objects, head, contract=contract,
                            component="not-declared" if fault == "component" else "serving", objects=objects)
    assert not objects.directory.exists()


def test_stage_is_fresh_and_retains_its_failed_copy(trained, tmp_path, monkeypatch):
    from behave.train import checkpoint_storage as module

    objects = DirectoryStore(tmp_path / "published")
    ref = publish(trained, objects)
    directories = []
    original = module.mkdtemp

    def create(**kwargs):
        result = original(**kwargs)
        directories.append(Path(result))
        return result

    monkeypatch.setattr(module, "mkdtemp", create)
    original_copy = module.copy_state_file

    def fail(reader, file, destination=None):
        if destination is not None and not isinstance(destination, BytesIO):
            destination.write(b"partial")
            raise OSError(errno.ENOSPC, "disk full")
        return original_copy(reader, file, destination)

    monkeypatch.setattr(module, "copy_state_file", fail)
    with pytest.raises(OSError) as error:
        stage(trained, objects, ref)
    assert error.value.errno == errno.ENOSPC
    assert len(directories) == 1 and directories[0].is_dir()
    assert any(path.read_bytes() == b"partial" for path in directories[0].rglob("*") if path.is_file())
    assert str(directories[0]) in error.value.__notes__[0]
    monkeypatch.setattr(module, "copy_state_file", original_copy)
    a, b = stage(trained, objects, ref), stage(trained, objects, ref)
    assert a.model_path != b.model_path and a.model_revision == b.model_revision
    assert all(directory.is_dir() for directory in directories)


def test_publication_schema_is_closed_and_canonical(trained, tmp_path):
    objects = DirectoryStore(tmp_path / "published")
    ref = publish(trained, objects)
    raw = objects.read(ref)
    for data in (raw + b"\n", canonical_json(parse_json(raw) | {"latest": True}),
                 canonical_json(parse_json(raw) | {"schema": "behave.weight-publication/0"})):
        with pytest.raises(RecordError):
            WeightPublication.from_bytes(data)
    large = replace(WeightPublication.from_bytes(raw), component="x" * (64 << 10))
    assert WeightPublication.from_bytes(large.to_bytes()) == large
    # Rank bounds come from the selected distributed checkpoint, not uint8.
    declared = replace(WeightPublication.from_bytes(raw), rank=256)
    assert WeightPublication.from_bytes(declared.to_bytes()) == declared
    for rank in (-1, True):
        with pytest.raises(ValueError):
            replace(declared, rank=rank)


def test_publication_binding_reaches_service_launch_and_endpoint(trained, tmp_path, lifecycle):  # noqa: F811
    from behave.serving import ServingSession

    objects = DirectoryStore(tmp_path / "published")
    ref = publish(trained, objects)
    bound = stage(trained, objects, ref)
    session = ServingSession(bound, scratch=tmp_path, environment={}, records=tmp_path / "service")
    try:
        endpoint = session.start()
        launch = parse_json((tmp_path / "service/launch.json").read_bytes())
        assert launch["spec"]["model_revision"] == ref.sha256
        assert launch["identity"] == endpoint.runtime_revision == bound.identity
        assert launch["argv"] == list(bound.command())
        assert Path(bound.model_path).is_dir()
    finally:
        stopped = session.close()
        assert stopped["cleanup_complete"] and Path(bound.model_path).is_dir()
