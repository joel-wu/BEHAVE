"""Pure fixture publications; no simulator, synthesis worker or model service."""

from dataclasses import replace

import pytest

from behave.core.artifacts import BlobRef, DirectoryReader, FileRef, SourceArtifact
from behave.data.published import ReleaseManifest, TaskManifest, TaskRevision


class FixturePublication:
    def __init__(self, root):
        self.root = root
        root.mkdir()
        self.reader = DirectoryReader(root)
        self.task = TaskManifest(
            task_id="fixture_counter",
            spec=self.file("spec.md", b"PUBLIC: count the accepted transfers."),
            contract=self.file("task.toml", b"version = 1\n"),
            public_files=(self.file("include/interface.bin", b"\x00\xff"),),
            reference_behavior=SourceArtifact((
                self.file("model.py", b"raise AssertionError('PRIVATE_BEHAVIOR_MUST_NOT_EXECUTE')"),
                self.file("helper.py", b"PRIVATE_HELPER = 7"),
            ), "model.py", "Counter"),
            reference_rtl=SourceArtifact((self.file("top.sv", b"// PRIVATE_REFERENCE_RTL"),), "top.sv", "top"),
            run_policy=self.put(b"PRIVATE_RUN_POLICY"),
            judge_policy=self.put(b"PRIVATE_JUDGE_POLICY"),
            metadata={"family": "counter", "tier": 1, "refs": ["upstream/source.c"],
                      "licence": "fixture", "provenance": {"note": "PRIVATE_PROVENANCE"}},
        )
        self.policy = self.put(b"PRIVATE_RELEASE_POLICY")
        self.release, self.ref = self.publish()

    def put(self, payload):
        ref = BlobRef.of(payload)
        (self.root / ref.sha256).write_bytes(payload)
        return ref

    def file(self, path, payload):
        return FileRef(path, self.put(payload))

    def publish(self, task=None, **release_changes):
        task = self.task if task is None else task
        revision = TaskRevision(task.task_id, self.put(task.to_bytes()))
        release = ReleaseManifest("fixture-release", (revision,), self.policy,
                                  {"split": "fixture-only"})
        release = replace(release, **release_changes)
        return release, self.put(release.to_bytes())


@pytest.fixture
def publication(tmp_path):
    return FixturePublication(tmp_path / "objects")
