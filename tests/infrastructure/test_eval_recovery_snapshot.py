"""Freeze the complete recovery dependency set without changing CPU behavior."""

import pytest

from tools.eval_recovery_snapshot import recovery_dependency_overrides


PREFIX = "src/behave/task_execution/"
BATCH = (
    "# Frozen CPU/resource implementation must survive.\n"
    "        pending_jobs = tuple((index, job) for index, job in enumerate(plan.jobs) if index not in completed)\n"
    "        frozen_cpu_api()\n"
)
REJUDGE = (
    "# Frozen simulation behavior must survive.\n"
    '    raw_plan["jobs"] = [job for job, _ in chosen]\n'
    "    frozen_simulation_api()\n"
)


def snapshot_fixture(tmp_path):
    base, repository = tmp_path / "base", tmp_path / "repository"
    for root in (base, repository):
        (root / PREFIX).mkdir(parents=True)
    (base / PREFIX / "batch.py").write_text(BATCH)
    (base / PREFIX / "rejudge.py").write_text(REJUDGE)
    for name in ("batch_plan.py", "batch_report.py"):
        (repository / PREFIX / name).write_bytes(("reviewed " + name).encode())
    return base, repository


def test_recovery_freeze_includes_reader_writer_and_scorer(tmp_path):
    base, repository = snapshot_fixture(tmp_path)
    before = {p: p.read_bytes() for p in (base / PREFIX).iterdir()}
    overrides = recovery_dependency_overrides(base, repository)
    assert set(overrides) == {PREFIX + name for name in (
        "batch_plan.py", "batch_report.py", "batch.py", "rejudge.py")}
    for name in ("batch_plan.py", "batch_report.py"):
        assert overrides[PREFIX + name] == (repository / PREFIX / name).read_bytes()
    assert b"recorded admission requires an imported or replayed case" in overrides[PREFIX + "batch.py"]
    assert b'raw_plan["recorded_admissions"] = [' in overrides[PREFIX + "rejudge.py"]
    assert b"frozen_cpu_api()" in overrides[PREFIX + "batch.py"]
    assert b"frozen_simulation_api()" in overrides[PREFIX + "rejudge.py"]
    assert {p: p.read_bytes() for p in before} == before


def test_recovery_freeze_is_idempotent(tmp_path):
    base, repository = snapshot_fixture(tmp_path)
    overrides = recovery_dependency_overrides(base, repository)
    for relative, content in overrides.items():
        (base / relative).write_bytes(content)
    assert recovery_dependency_overrides(base, repository) == overrides


@pytest.mark.parametrize("name", ["batch.py", "rejudge.py"])
def test_recovery_freeze_rejects_unknown_frozen_implementation(tmp_path, name):
    base, repository = snapshot_fixture(tmp_path)
    (base / PREFIX / name).write_text("unrecognized implementation\n")
    with pytest.raises(ValueError, match="unknown frozen recovery dependency"):
        recovery_dependency_overrides(base, repository)
