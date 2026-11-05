"""Current source CLI supports Bash acquisition, not retired discovery stages."""

import subprocess

import pytest
from test_improvement_acquisition import configuration
from test_role_process import job_for
from test_source_discovery import seed as discovery_seed
from test_source_selection import seed as selection_seed

from behave.core.records import parse_json
from behave.data.__main__ import main
from behave.data.role import input_closure
from behave.improve.acquisition import AcquisitionConfiguration


@pytest.mark.parametrize("stage", ["selection", "discovery"])
def test_retired_source_stages_are_not_reenabled_by_cli_cleanup(tmp_path, stage):
    target = tmp_path / "run"
    with pytest.raises(SystemExit) as error:
        main(["--stage", stage, "--configuration", "not-read", "--source-objects", "not-read",
              "--run-directory", str(target), "--dry-run"])
    assert error.value.code == 2 and not target.exists()


@pytest.mark.parametrize("seed", [discovery_seed, selection_seed])
def test_legacy_records_remain_non_executable(tmp_path, seed):
    store, inputs = seed(tmp_path)
    with pytest.raises(ValueError, match="retired; use Bash acquisition"):
        input_closure(job_for(inputs, []), store)


def test_current_acquisition_dry_run_has_no_process_or_run_side_effects(tmp_path, publication, monkeypatch, capsys):
    store, role, _, _ = configuration(tmp_path, publication)
    config = AcquisitionConfiguration(role)
    path = tmp_path / "configuration.json"
    path.write_bytes(config.to_bytes())
    target = tmp_path / "run"
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("dry-run cannot dispatch"))
    assert main(["--stage", "acquisition", "--configuration", str(path), "--source-objects", str(store.directory),
                 "--run-directory", str(target), "--dry-run"]) == 0
    report = parse_json(capsys.readouterr().out.encode())
    assert report["status"] == "validated" and report["attempt_id"] == role.attempt.attempt_id
    assert not target.exists()
