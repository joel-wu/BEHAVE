"""Test-owned fixtures; contract elaboration uses the production EDA lane."""

import shutil

import pytest

from behave.core import paths
from behave.sim.declarations import contract_of_task, declarations_at
from tests.support.scoring_task import stage_handshake_register


@pytest.mark.cosim
def test_scoring_fixture_is_complete_without_dataset_lookup(monkeypatch):
    def unexpected_dataset():
        raise AssertionError("a scoring regression must not consult benchmark data")

    monkeypatch.setattr(paths, "problems_root", unexpected_dataset)
    problem = stage_handshake_register()
    try:
        declarations = declarations_at(problem)
        contract = contract_of_task(problem, declarations=declarations)
        assert declarations.reset_restores is True
        assert not (problem / "run.toml").exists()
        assert not {"horizon", "queue_bound", "drain", "reset_hold_cycles"} & declarations.as_dict().keys()
        assert [field.port for field in contract.inputs()[0].fields] == ["in_d"]
        assert [field.port for field in contract.outputs()[0].fields] == ["out_q"]
        assert contract.outputs()[0].answers_per_transaction == 1
        assert len(contract.clocks) == 1
        assert (problem / "behavior.py").is_file()
        assert (problem / "rtl.sv").is_file()
        assert (problem / "drop_fourth.sv.txt").is_file()
    finally:
        shutil.rmtree(problem)


def test_scoring_fixture_mutation_is_private_to_one_copy():
    first, second = stage_handshake_register(), stage_handshake_register()
    try:
        assert first != second
        original = (second / "rtl.sv").read_bytes()
        shutil.copyfile(first / "drop_fourth.sv.txt", first / "rtl.sv")
        assert (first / "rtl.sv").read_bytes() != original
        assert (second / "rtl.sv").read_bytes() == original
        third = stage_handshake_register()
        try:
            assert (third / "rtl.sv").read_bytes() == original
        finally:
            shutil.rmtree(third)
    finally:
        shutil.rmtree(first)
        shutil.rmtree(second)
