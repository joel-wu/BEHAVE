"""Task-owned interface declarations and their provenance; execution policy is separate."""
import pathlib

import pytest

from behave.sim.core.refusal import Refused

from .test_wave1_closure import _repo

_NOT_ONE_TO_ONE = {
    "per N": "Per four accepted input transactions, the design emits exactly one "
             "output transaction.",
    "one in every N": "One in every four accepted input transactions produces exactly "
                      "one output transaction.",
    "grouped": "The module groups inputs: for each group of eight accepted input "
               "transactions it produces exactly one output transaction.",
    "warm-up": "After a warm-up of three accepted input transactions, each accepted "
               "input transaction produces exactly one output transaction.",
    "pipeline fill": "Once the pipeline is full, every accepted input transaction "
                     "produces exactly one output transaction.",
    "conditional": "When the enable bit is set, each accepted input transaction "
                   "produces exactly one output transaction.",
    "final only": "The last accepted input transaction of a burst produces exactly one "
                  "output transaction.",
}


_ONE_TO_ONE = {
    "each ... produces": "Each accepted input transaction produces exactly one output "
                         "transaction.",
    "one ... produces": "One accepted input transaction produces exactly one output "
                        "transaction.",
    "for each, subject": "For each accepted input transaction, the module produces "
                         "exactly one output transaction.",
    "every ... produces": "Every accepted input transaction produces exactly one "
                          "output transaction.",
    "labelled": "The module is 1:1: every accepted input transaction produces exactly "
                "one output transaction.",
    "passive": "For every accepted input transaction, exactly one output transaction "
               "is produced.",
    "always": "One accepted input transaction always produces exactly one output "
              "transaction.",
}


@pytest.fixture
def policy_problem(tmp_path):
    """Policy validation uses an owned declaration, not the first corpus task."""
    import shutil

    source = tmp_path / "policy"
    source.mkdir()
    overlays = _repo() / "tools" / "declarations"
    shutil.copy2(overlays / "count10.toml", source / "task.toml")
    return source


@pytest.fixture
def generated_problem(policy_problem):
    """A real reference-derived declaration with a small, fixed interface."""
    from behave.sim.contract_of import contract_of_spec

    from .test_reference_rules import INTERFACE, VARIABLE_CARDINALITY, _task_text

    boundary = contract_of_spec(INTERFACE, "manifest-test")
    (policy_problem / "spec.md").write_text(INTERFACE)
    (policy_problem / "behavior.py").write_text(VARIABLE_CARDINALITY)
    (policy_problem / "task.toml").write_text(
        _task_text(boundary, VARIABLE_CARDINALITY))
    return policy_problem


def test_required_declarations_do_not_invent_a_task_contract(tmp_path):
    from behave.sim.declarations import _task_facts_at

    (tmp_path / "spec.md").write_text("# nothing\n")
    (tmp_path / "rtl.sv").write_text("module m; endmodule\n")
    assert _task_facts_at(tmp_path, require_declarations=True) == (None, None)


def test_declarations_serialise_as_words_not_enum_objects():
    """They travel into the evaluation's telemetry and from there into
    `cosim_report.json`. A `Kind` object reaches `json.dumps` and raises, which took down
    every run of a task that actually carried declarations -- the only runs the
    authoritative path can score. Found by running one, not by reading it."""
    import json
    import shutil
    import tempfile

    from behave.sim.declarations import declarations_at

    root = _repo() / "tools" / "declarations"

    with tempfile.TemporaryDirectory() as where:
        shutil.copy2(root / "count10.toml", pathlib.Path(where) / "task.toml")
        declarations = declarations_at(where)
    body = json.dumps(declarations.as_dict())
    assert '"transaction"' in body and '"ordered"' in body and '"clears"' in body
    assert json.loads(body)["endpoints"]["out"]["answers_per_transaction"] == 1


@pytest.mark.cosim
def test_a_manifest_that_lies_about_where_it_came_from_is_refused(generated_problem):
    """Reference-derived provenance is checked rather than displayed."""
    import shutil
    import tempfile

    from behave.sim.declarations import declarations_at
    from behave.sim.reference_rules import GENERATOR

    source = generated_problem

    def read_with(edit):
        with tempfile.TemporaryDirectory() as where:
            staged = pathlib.Path(where) / source.name
            shutil.copytree(source, staged)
            (staged / "task.toml").write_text(edit((staged / "task.toml").read_text()))
            return declarations_at(staged)

    # Untouched, it reads -- and says what it came from.
    got = read_with(lambda text: text)
    assert dict(got.generated)["written_by"] == "tools/declare_tasks.py"
    assert dict(got.generated)["behavior_digest"]
    assert dict(got.generated)["interface_digest"]

    marker = f"generator = {GENERATOR}"
    with pytest.raises(Refused, match="declarations-generator-unknown"):
        read_with(lambda text: text.replace(marker, "generator = 999"))
    with pytest.raises(Refused, match="declarations-behavior-changed"):
        read_with(lambda text: "\n".join(
            line if not line.startswith("behavior_digest")
            else 'behavior_digest = "deadbeefdeadbeef"'
            for line in text.splitlines()))
    with pytest.raises(Refused, match="declarations-unknown-section"):
        read_with(lambda text: text + '\n[bogus]\nwhat = "?"\n')
    with pytest.raises(Refused, match="declarations-unknown-provenance-field"):
        read_with(lambda text: text.replace(
            marker, f'{marker}\nwho = "me"'))


def test_a_hand_written_manifest_needs_no_provenance_and_says_so():
    """The six overlays are written by a person, and what they claim is claimed by that
    person. Refusing them for carrying no `[generated]` block would be demanding a
    machine's provenance for a human's document -- but the record still has to say which
    it is, so a reader of a verdict knows what stands behind its declarations."""
    import shutil
    import tempfile

    from behave.sim.declarations import declarations_at

    overlays = _repo() / "tools" / "declarations"
    with tempfile.TemporaryDirectory() as where:
        staged = pathlib.Path(where)
        shutil.copy2(overlays / "dualedge.toml", staged / "task.toml")
        got = declarations_at(staged)
    assert dict(got.generated)["written_by"] == "hand"
    assert "policy_digest" not in dict(got.generated)
    assert not {"drain", "horizon", "queue_bound", "stall_every"} & got.as_dict().keys()


@pytest.mark.parametrize("section", ["run", "runtime", "generation"])
def test_task_file_cannot_set_runtime_policy(policy_problem, section):
    from behave.sim.declarations import declarations_at

    path = policy_problem / "task.toml"
    path.write_text(path.read_text() + f"\n[{section}]\nhorizon = 256\n")
    with pytest.raises(Refused, match="declarations-unknown-section"):
        declarations_at(policy_problem)


@pytest.mark.cosim
def test_a_manifest_is_re_derived_and_not_taken_on_trust(generated_problem):
    """Every generated semantic field is re-derived from Behavior and the interface."""
    import shutil
    import tempfile

    from behave.sim.declarations import declarations_at

    source = generated_problem

    def read_with(edit):
        with tempfile.TemporaryDirectory() as where:
            staged = pathlib.Path(where) / source.name
            shutil.copytree(source, staged)
            (staged / "task.toml").write_text(edit((staged / "task.toml").read_text()))
            return declarations_at(staged)

    got = read_with(lambda text: text)
    assert dict(got.generated)["fields_rederived"] == 3

    with pytest.raises(Refused, match="declarations-do-not-match-the-reference"):
        read_with(lambda text: text.replace('matching = "ordered"',
                                            'matching = "content"'))
    with pytest.raises(Refused, match="declarations-do-not-match-the-reference"):
        read_with(lambda text: text.replace('kind = "transaction"',
                                            'kind = "sampled"', 1))


def test_legacy_prose_manifests_remain_rederived_compatibility_inputs(tmp_path):
    """Generator 5 is readable only under its own executable derivation rule."""
    from behave.sim.declarations import declarations_at
    from behave.sim.spec_rules import GENERATOR, spec_digest

    spec = (
        "Each accepted input transaction produces exactly one output transaction.\n"
    )
    (tmp_path / "spec.md").write_text(spec)
    (tmp_path / "task.toml").write_text(
        "[generated]\n"
        f"generator = {GENERATOR}\n"
        f'spec_digest = "{spec_digest(spec)}"\n\n'
        "[endpoint.in]\nkind = \"transaction\"\n\n"
        "[endpoint.out]\nkind = \"transaction\"\n"
        "answers_per_transaction = 1\n"
    )

    assert declarations_at(tmp_path).for_endpoint("out")[
        "answers_per_transaction"] == 1
    (tmp_path / "task.toml").write_text(
        (tmp_path / "task.toml").read_text().replace(
            "answers_per_transaction = 1", "answers_per_transaction = 2"))
    with pytest.raises(Refused, match="declarations-do-not-match-the-specification"):
        declarations_at(tmp_path)


def test_the_ratio_rule_reads_only_what_it_can_read_without_judgement():
    """`each|every|one|per ... accepted input ... exactly one output transaction`, with
    anything allowed in between, read a ratio out of sentences that state the opposite.
    Reproduced: "Per four accepted input transactions, the design emits exactly one
    output transaction" came back `answers_per_transaction = 1`, and so did "One in
    every four accepted input transactions produces exactly one output transaction".

    A quantifier is not universal because it is spelled `one` or `per`. The corpus below
    is the adversarial half of this rule and is meant to grow: every time a sentence
    fools it, the sentence goes in here.
    """
    from behave.sim.spec_rules import derive

    preamble = "Each accepted input transaction carries one word.\n"
    for name, sentence in _NOT_ONE_TO_ONE.items():
        fields, _cited, missing = derive(preamble + sentence)
        assert fields.get("endpoint.out.answers_per_transaction") is None, \
            f"{name}: read a 1:1 ratio out of {sentence!r}"
        assert missing, name

    for name, sentence in _ONE_TO_ONE.items():
        fields, _cited, missing = derive(preamble + sentence)
        assert fields.get("endpoint.out.answers_per_transaction") == 1, \
            f"{name}: {missing}"


@pytest.mark.cosim
def test_a_manifest_that_names_no_author_is_not_read(generated_problem):
    """The absence of `[generated]` *meant* hand-written, so deleting four lines turned
    a generated manifest into one nothing re-derives -- the strongest check in the module
    was opt-out by deletion. A file now says which it is, and a hand-written one names
    who wrote it and why."""
    import shutil
    import tempfile

    from behave.sim.declarations import declarations_at

    source = generated_problem

    def read_with(edit):
        with tempfile.TemporaryDirectory() as where:
            staged = pathlib.Path(where) / source.name
            shutil.copytree(source, staged)
            (staged / "task.toml").write_text(edit((staged / "task.toml").read_text()))
            return declarations_at(staged)

    def drop_generated(text):
        out, skip = [], False
        for line in text.splitlines():
            if line.strip() == "[generated]":
                skip = True
                continue
            if skip and (line.startswith("[") or not line.strip()):
                skip = False
            if not skip:
                out.append(line)
        return "\n".join(out)

    with pytest.raises(Refused, match="declarations-provenance-absent"):
        read_with(drop_generated)
    # A hand-written claim is read, and it has to say whose claim it is.
    with pytest.raises(Refused, match="declarations-hand-written-without-an-author"):
        read_with(lambda text: drop_generated(text)
                  + '\n[written_by_hand]\nwho = "a person"\n')
    got = read_with(lambda text: drop_generated(text)
                    + '\n[written_by_hand]\nwho = "a person"\nwhy = "a reason"\n')
    assert dict(got.generated)["written_by"] == "hand"
    assert dict(got.generated)["who"] == "a person"


def test_a_count_that_is_not_a_count_is_refused(policy_problem):
    """`int(block[key])` took whatever was there, so `response_deadline = -5` -- a
    deadline elapsed before it starts -- went into the contract and decided verdicts."""
    import shutil
    import tempfile

    from behave.sim.declarations import declarations_at

    source = policy_problem

    def read_with(line):
        with tempfile.TemporaryDirectory() as where:
            staged = pathlib.Path(where) / source.name
            shutil.copytree(source, staged)
            text = (staged / "task.toml").read_text().replace(
                'kind = "transaction"', 'kind = "transaction"\n' + line, 1)
            (staged / "task.toml").write_text(text)
            return declarations_at(staged)

    for line in ("response_deadline = -5", "max_outstanding = 0",
                 "answers_per_transaction = 0"):
        with pytest.raises(Refused, match="declarations-out-of-range"):
            read_with(line)


def test_reset_restores_is_a_claim_about_the_task_not_a_knob_of_the_run():
    """It lived in `run.toml`, beside the horizon and the stall pattern -- a statement a
    run can find false, filed among the numbers an operator turns. It is in `task.toml`
    now, under the specification's digest, where a design can be wrong about it."""
    import shutil
    import tempfile

    from behave.sim.declarations import declarations_at

    overlays = _repo() / "tools" / "declarations"
    with tempfile.TemporaryDirectory() as where:
        staged = pathlib.Path(where)
        shutil.copy2(overlays / "dualedge.toml", staged / "task.toml")
        got = declarations_at(staged)
        assert got.reset_restores is True
        # A neighboring obsolete file cannot override the task claim.
        (staged / "run.toml").write_text("reset_restores = false\n")
        assert declarations_at(staged).reset_restores is True
