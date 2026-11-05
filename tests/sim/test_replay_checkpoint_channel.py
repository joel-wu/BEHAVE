"""The proposed checkpoint channel does not execute or trust candidate data."""

from threading import Event
from types import SimpleNamespace

import pytest

from behave.sim.rtl.replay_checkpoint import CheckpointService, _create, _read
from behave.sim.scoring_policy import ScoringStopped


def test_reader_does_not_follow_candidate_symlinks(tmp_path):
    secret = tmp_path / "outside"
    secret.write_text("not a replay record")
    channel = tmp_path / "channel"
    channel.mkdir()
    (channel / "trace.json").symlink_to(secret)
    with pytest.raises(OSError):
        _read(channel / "trace.json")


def test_reader_rejects_nonregular_files_without_blocking(tmp_path):
    import os

    pipe = tmp_path / "trace.json"
    os.mkfifo(pipe)
    with pytest.raises(ValueError, match="regular file"):
        _read(pipe)


@pytest.mark.parametrize("link", ["symlink", "hardlink"])
def test_ack_writer_cannot_overwrite_a_candidate_selected_target(tmp_path, link):
    target = tmp_path / "outside"
    target.write_text("preserve")
    path = tmp_path / "ack.partial"
    if link == "symlink":
        path.symlink_to(target)
    else:
        path.hardlink_to(target)
    with pytest.raises(FileExistsError):
        _create(path, b"1")
    assert target.read_text() == "preserve"


def test_host_assesses_before_acknowledging_and_joins_supervisor(tmp_path):
    resumed, finished = Event(), Event()
    body = b'{"untrusted":true}'
    (tmp_path / "trace.json").write_bytes(body)
    (tmp_path / "ready").write_text("1")
    receipt = SimpleNamespace(cleanup_confirmed=True)

    def execute():
        assert resumed.wait(2)
        finished.set()
        return receipt

    def consume(payload):
        assert payload == body
        assert not (tmp_path / "ack").exists()
        resumed.set()

    service = CheckpointService(consume, lambda: pytest.fail("unexpected invalidation"))
    assert service.run(execute, tmp_path) is receipt
    assert service.failure is None
    assert finished.is_set()
    assert (tmp_path / "ack").read_text() == "1"


@pytest.mark.parametrize("failure", [ScoringStopped(), ValueError("bad evidence")])
def test_callback_failure_requests_stop_and_waits_for_cleanup(tmp_path, failure):
    from time import sleep

    finished = Event()
    (tmp_path / "trace.json").write_text("{}")
    (tmp_path / "ready").write_text("1")

    def execute():
        while not (tmp_path / "stop").exists():
            sleep(0.001)
        finished.set()
        return SimpleNamespace(cleanup_confirmed=True)

    def consume(_):
        raise failure

    service = CheckpointService(consume, lambda: None)
    assert service.run(execute, tmp_path).cleanup_confirmed
    assert finished.is_set()
    assert service.failure is failure
    assert not (tmp_path / "ack").exists()


def test_late_failure_is_not_hidden_by_earlier_checkpoint(tmp_path):
    invalidations = []

    def execute():
        (tmp_path / "unsafe").touch()
        return SimpleNamespace(cleanup_confirmed=True)

    service = CheckpointService(lambda _: None, lambda: invalidations.append(True))
    assert service.run(execute, tmp_path).cleanup_confirmed
    assert invalidations == [True]


def test_a_violation_withdraws_the_prefix_without_stopping_the_guest(tmp_path):
    # The guest's completed record is the failure evidence. Stopping it left
    # only the placeholder no-result, so a really failing input was not scored.
    invalidated = Event()

    def execute():
        (tmp_path / "unsafe").touch()
        assert invalidated.wait(2)
        return SimpleNamespace(cleanup_confirmed=True)

    service = CheckpointService(lambda _: pytest.fail("no checkpoint was published"),
                                invalidated.set)
    assert service.run(execute, tmp_path).cleanup_confirmed
    assert not (tmp_path / "stop").exists()
    assert service.stop_requested is False


def test_the_guest_publishes_no_checkpoint_after_announcing_a_violation(tmp_path):
    from behave.sim.rtl.replay_checkpoint import CheckpointClient

    completion = SimpleNamespace(outputs=(("y", None),),
                                 at_checkpoint_boundary=lambda *_args, **_options: True)
    client = CheckpointClient(completion)
    client.directory = tmp_path
    counts = {"accepted": {"x": 4}, "emitted": {"y": 4}}
    assert client.due(counts, memory_quiescent=True)
    client.invalidate()
    assert (tmp_path / "unsafe").exists()
    # The host acknowledges nothing after this, so a publish would block.
    assert not client.due(counts, memory_quiescent=True)
