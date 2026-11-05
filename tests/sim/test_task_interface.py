"""Task files select the same immutable physical contract as public callers."""

import hashlib
import json
import os
import pickle
from pathlib import Path

import pytest

from behave.sim import api
from behave.sim.core.protocol import adapter_program_for
from behave.sim.declarations import declared_contract
from tests.sim.test_apb_protocol import _contract as apb_contract
from tests.sim.test_axi_lite_protocol import _contract as axil_contract
from tests.sim.test_interface_composition import _contract as composed_contract
from tests.sim.test_protocol_adapters import _contract as simple_contract


def write_interface(directory, contract, *, payload=None, extra=""):
    payload = json.dumps(contract.as_dict()).encode() if payload is None else payload
    (directory / "interface.json").write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    (directory / "task.toml").write_text(f'''[interface]
version = 1
file = "interface.json"
sha256 = "{digest}"
[written_by_hand]
who = "fixture task author"
why = "explicit physical and transaction semantics"
{extra}
''')
    return digest


@pytest.mark.parametrize("build", [apb_contract, axil_contract, composed_contract,
                                  lambda: simple_contract("ready-valid"),
                                  lambda: simple_contract("start-done")])
def test_task_file_round_trip_uses_the_existing_contract_and_adapter(tmp_path, build):
    expected = build()
    digest = write_interface(tmp_path, expected)
    declarations = api.declarations_at(tmp_path)
    contract = api.contract_of_task(tmp_path, declarations=declarations)
    assert contract.as_dict() == expected.as_dict()
    assert contract.digest == expected.digest
    assert adapter_program_for(contract).identity == adapter_program_for(expected).identity
    assert declarations.generated["interface_sha256"] == digest
    assert declarations.as_dict()["interface_contract"] == expected.as_dict()
    assert pickle.loads(pickle.dumps(declarations)).as_dict() == declarations.as_dict()
    assert declared_contract(expected, declarations) is contract
    # No second read during preparation, even if the task files subsequently change.
    (tmp_path / "interface.json").write_text("invalid later revision")
    assert api.contract_of_task(tmp_path, declarations=declarations) is contract
    with pytest.raises(api.Refused) as caught:
        api.contract_of_task(tmp_path)
    assert caught.value.refusal.kind == "declarations-interface-changed"


@pytest.mark.parametrize("extra, code", [
    ("[endpoint.in]\nmax_outstanding = 2", "declarations-interface-overridden"),
    ("[clock]\nreset_restores = true", "declarations-interface-overridden"),
    ("[generated]\ngenerator = 5", "declarations-interface-not-derived"),
])
def test_explicit_interface_has_no_parallel_overlay_or_borrowed_derivation(tmp_path, extra, code):
    write_interface(tmp_path, apb_contract(), extra=extra)
    with pytest.raises(api.Refused) as caught:
        api.declarations_at(tmp_path)
    assert caught.value.refusal.kind == code


@pytest.mark.parametrize("change", [
    lambda text: text.replace("version = 1", "version = true"),
    lambda text: text.replace("version = 1", "version = 2"),
    lambda text: text.replace('file = "interface.json"', 'file = "../interface.json"'),
    lambda text: text.replace('file = "interface.json"', 'file = "/tmp/interface.json"'),
    lambda text: text.replace("[interface]", "[interface]\nunknown = 1"),
])
def test_interface_descriptor_is_strict(tmp_path, change):
    write_interface(tmp_path, apb_contract())
    path = tmp_path / "task.toml"
    path.write_text(change(path.read_text()))
    with pytest.raises(api.Refused) as caught:
        api.declarations_at(tmp_path)
    assert caught.value.refusal.kind in {
        "declarations-interface-invalid", "declarations-interface-path-invalid"}


def test_interface_symlink_cannot_leave_the_task(tmp_path):
    task = tmp_path / "task"
    task.mkdir()
    write_interface(task, apb_contract())
    (task / "interface.json").rename(tmp_path / "outside.json")
    (task / "interface.json").symlink_to(tmp_path / "outside.json")
    with pytest.raises(api.Refused) as caught:
        api.declarations_at(task)
    assert caught.value.refusal.kind == "declarations-interface-path-invalid"


@pytest.mark.parametrize("kind", ["directory", "fifo"])
def test_interface_is_a_regular_document_not_a_blocking_stream(tmp_path, kind):
    write_interface(tmp_path, apb_contract())
    path = tmp_path / "interface.json"
    path.rename(tmp_path / "original.json")
    if kind == "directory":
        path.mkdir()
    else:
        os.mkfifo(path)
    with pytest.raises(api.Refused) as caught:
        api.declarations_at(tmp_path)
    assert caught.value.refusal.kind == "declarations-interface-unreadable"


@pytest.mark.parametrize("payload", [
    b'{"version": "task-contract/7", "version": "task-contract/7"}',
    b'{"value": NaN}', b'[]', b'{}',
])
def test_matching_hash_does_not_replace_strict_contract_decoding(tmp_path, payload):
    write_interface(tmp_path, apb_contract(), payload=payload)
    with pytest.raises(api.Refused) as caught:
        api.declarations_at(tmp_path)
    assert caught.value.refusal.kind in {
        "declarations-interface-invalid", "contract-version-unsupported"}


def test_task_authority_and_semantic_identity_cannot_be_overridden(tmp_path):
    contract = apb_contract()
    image = contract.as_dict()
    image["authority"] = "candidate"
    write_interface(tmp_path, contract, payload=json.dumps(image).encode())
    with pytest.raises(api.Refused) as caught:
        api.declarations_at(tmp_path)
    assert caught.value.refusal.kind == "declarations-interface-not-task-owned"
    write_interface(tmp_path, contract)
    declarations = api.declarations_at(tmp_path)
    changed = apb_contract(wait_limit=3)
    with pytest.raises(api.Refused) as caught:
        declared_contract(changed, declarations)
    assert caught.value.refusal.kind == "declarations-interface-conflict"
    with pytest.raises(api.Refused) as caught:
        api.Declarations(interface_contract=contract, endpoints={"in": {"max_outstanding": 2}})
    assert caught.value.refusal.kind == "declarations-interface-overridden"
    write_interface(tmp_path, changed)
    assert api.contract_of_task(tmp_path).digest != contract.digest


def test_legacy_task_declarations_keep_their_existing_image(tmp_path):
    (tmp_path / "task.toml").write_text('''[written_by_hand]
who = "fixture"
why = "legacy endpoint declarations"
[endpoint.in]
kind = "transaction"
''')
    declarations = api.declarations_at(tmp_path)
    assert declarations.interface_contract is None
    assert "interface_contract" not in declarations.as_dict()
    expected = simple_contract("ready-valid")
    assert declared_contract(expected, declarations).digest == expected.digest


@pytest.mark.parametrize("build", [apb_contract, axil_contract, composed_contract,
                                  lambda: simple_contract("ready-valid"),
                                  lambda: simple_contract("start-done")])
def test_native_interface_needs_no_toml_or_spec_reconstruction(tmp_path, build):
    expected = build()
    (tmp_path / "interface.json").write_text(json.dumps(expected.as_dict()))
    declarations = api.declarations_at(tmp_path)
    assert declarations.interface_contract.as_dict() == expected.as_dict()
    assert api.contract_of_task(tmp_path).as_dict() == expected.as_dict()
    assert api.contract_of_task(tmp_path, declarations=declarations).digest == expected.digest
    assert not (tmp_path / "task.toml").exists()


def test_native_interface_invalid_json_does_not_fall_back_to_spec(tmp_path):
    (tmp_path / "interface.json").write_text('{}')
    (tmp_path / "spec.md").write_text("Must not be used to replace the declared interface.")
    with pytest.raises(api.Refused) as caught:
        api.contract_of_task(tmp_path)
    assert caught.value.refusal.kind == "contract-version-unsupported"


def test_explicit_task_contract_is_shared_by_behavior_scoring(tmp_path):
    from tests.sim.test_rtl_public_analysis_path import _BaselineOnly

    write_interface(tmp_path, apb_contract())
    contract = api.contract_of_task(tmp_path)
    source = """class Model:
    def reset(self): pass
    def process(self, paddr):
        return {'prdata': (paddr * 2) & 255, 'pslverr': int(paddr >= 128)}
"""
    candidate = tmp_path / "candidate.py"
    candidate.write_text(source)
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
                                   {"behavior.py": source.encode()}, config={"class": "Model"})
    task = api.admit(contract, api.realization_of_behavior_source(
        source, contract, file="candidate.py"), reference=reference)
    handle = api.prepare_behavior(
        task, api.BehaviorConfig(candidate, file="candidate.py"),
        api.GenerationSpec(horizon=8, queue_bound=1, reset_hold_cycles=1, drain=4))
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "run", reference=reference,
        baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=1),
        providers=(_BaselineOnly(),), probe_budget=0))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.evaluation.passed, result.evaluation.as_dict()
    assert json.loads(Path(result.record_path).read_text())["authority"]["passed"] is True
