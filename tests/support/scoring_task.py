"""Test-owned scoring tasks, independent of the editable benchmark population."""

import shutil
import tempfile
from pathlib import Path


def stage_handshake_register() -> Path:
    """Copy a fixed 1:1 ready/valid task into a unique writable directory.

    Callers may mutate this copy and own its cleanup. The complete declaration
    and run policy travel with the fixture; no dataset search or overlay occurs.
    """
    source = Path(__file__).resolve().parents[1] / "sim/fixtures/handshake_register"
    destination = Path(tempfile.mkdtemp(prefix="behave-score-register-"))
    shutil.copytree(source, destination, dirs_exist_ok=True)
    return destination


def handshake_register_command(directory: Path, *, source_name="rtl.sv"):
    """A finite public RTL command over the fixed fixture, with no old coordinator."""
    from behave.sim import api

    fixture = Path(__file__).resolve().parents[1] / "sim/fixtures/handshake_register"
    directory.mkdir(parents=True, exist_ok=True)
    rtl = directory / "candidate.sv"
    rtl.write_bytes((fixture / source_name).read_bytes())
    contract = api.contract_of_task(fixture)
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": (fixture / "behavior.py").read_bytes()},
        config={"class": "BitRegister"})
    task = api.admit(contract, api.realization_of_rtl_source(
        rtl.read_text(), top="handshake_register", contract=contract), reference=reference)
    spec = api.GenerationSpec(64, 4, contract.clocks[0].reset_hold_cycles, 16)
    handle = api.prepare_rtl(task, api.RTLConfig(
        rtl, "handshake_register", directory / "build"), spec)
    return handle.command_request(
        run_directory=directory / "result", reference=reference,
        baseline=api.build_baseline(contract, spec, realization=task.realization,
            policy=api.BaselinePolicy(case_limit=2, max_queue_depth=4)),
        providers=(), search_policy=api.SearchPolicy(time_budget_s=0), run_analysis=False)
