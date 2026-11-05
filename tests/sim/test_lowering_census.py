"""The tracked lowering census and the stable refusal codes it groups by.

todo3 T3-11: replace the scratch census -- which regexed refusal sentences into
groups -- with one typed row per task under a stable rule identity. These tests hold
the two halves: every `LoweringError` raise site states a code (so the census can
never fall back to message text), and the census row itself is typed in all four
outcomes.
"""
import ast

from .test_wave1_closure import _repo, _tool_module


def _behavior_modules():
    root = _repo() / "src" / "behave" / "sim" / "behavior"
    return sorted(root.glob("*.py"))


def test_every_lowering_refusal_states_a_stable_code():
    """The census groups by `LoweringError.code`, so a raise site without one would
    surface as `unclassified` and silently merge with every other such site --
    exactly the message-text-shaped hole the codes exist to close. Walked over the
    source, so a new raise site cannot land without naming its rule; and the codes
    are kebab-case identifiers, not sentences, because a code that reads like prose
    invites editing, and editing an identity forks the census history.
    """
    # Planner modules raise their own typed errors and lowering preserves the
    # originating code at the public boundary. Scan those origins too;
    # requiring every forwarding site to replace ``exc.code`` with a new
    # literal would create a second rule authority rather than stabilize one.
    code_arguments = {
        "LoweringError": ("keyword", "code"),
        "SequencePlanError": ("positional", 2),
        "RecordPlanError": ("positional", 2),
        "BatchContractError": ("positional", 2),
        "_StaticIssue": ("positional", 0),
    }

    def code_of(call):
        kind, key = code_arguments[call.func.id]
        if kind == "keyword":
            return next((kw.value for kw in call.keywords if kw.arg == key),
                        None)
        return call.args[key] if len(call.args) > key else None

    def stable_literal(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            return (bool(value)
                    and all(part.isalnum() for part in value.split("-"))
                    and value == value.lower())
        # A condition may select between two named rules without synthesizing
        # either identity dynamically.
        if isinstance(node, ast.IfExp):
            return stable_literal(node.body) and stable_literal(node.orelse)
        return False

    def forwarded(call, node, parents):
        # ``exc.code`` forwards a typed planner error; ``status.code`` forwards
        # a _StaticIssue whose construction sites are scanned above. The
        # source-batch helper named ``error`` receives the literal rule from
        # each call site; its sole ``code`` parameter is the same forwarding
        # seam rather than an origin.
        if (isinstance(node, ast.Attribute) and node.attr == "code"
                and isinstance(node.value, ast.Name)
                and node.value.id in {"exc", "status"}):
            return call.func.id in {"LoweringError", "SequencePlanError"}
        if not (isinstance(node, ast.Name) and node.id == "code"
                and call.func.id == "BatchContractError"):
            return False
        owner = parents.get(call)
        while owner is not None and not isinstance(
                owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = parents.get(owner)
        return owner is not None and owner.name == "error"

    sites, missing, malformed = 0, [], []
    for path in _behavior_modules():
        tree = ast.parse(path.read_text())
        parents = {child: parent for parent in ast.walk(tree)
                   for child in ast.iter_child_nodes(parent)}
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in code_arguments):
                continue
            sites += 1
            code = code_of(node)
            where = f"{path.name}:{node.lineno}"
            if code is None:
                missing.append(where)
            elif not stable_literal(code) and not forwarded(
                    node, code, parents):
                malformed.append(where)
    assert sites >= 70, "the walk stopped finding the raise sites it exists to hold"
    assert not missing, f"raise sites without a stable code: {missing}"
    assert not malformed, f"codes that are not kebab-case literals: {malformed}"


def test_the_census_row_is_typed_in_every_outcome(tmp_path):
    """One row shape, four outcomes. A refusal carries the rule's code, the source
    site and the stage that raised; a lowering carries the IR size; a timeout is
    the typed refusal `analysis-timeout` (an analysis limit is not a claim about
    the subset); a crash is the frontend's own outcome, so a broken build cannot
    masquerade as principled refusals.
    """
    census = _tool_module("lowering_census")

    lowers = tmp_path / "lowers"
    lowers.mkdir()
    (lowers / "behavior.py").write_text(
        "class M:\n"
        "    def reset(self):\n"
        "        self.acc = 0\n"
        "    def process(self, a):\n"
        "        self.acc = (self.acc + a) & 0xFF\n"
        "        return {'out': self.acc}\n")
    row = census.census_row(lowers / "behavior.py")
    assert row["outcome"] == "lowered" and row["code"] is None
    assert row["ir_commands"] and row["ir_commands"] > 0
    assert (type(row["representation_cost"]) is int
            and row["representation_cost"] > row["ir_commands"])

    refuses = tmp_path / "refuses"
    refuses.mkdir()
    (refuses / "behavior.py").write_text(
        "class M:\n"
        "    def reset(self):\n"
        "        pass\n"
        "    def process(self, a):\n"
        "        return {'out': a / 3}\n")
    row = census.census_row(refuses / "behavior.py")
    assert row["outcome"] == "refused"
    assert row["code"] == "operator-unsupported", row
    assert row["site"]["line"] == 5
    assert row["stage"] == "lowering"
    assert row["representation_cost"] is None

    # The timeout is a typed row, not an exception: a lowering that will not finish
    # inside the limit is recorded as `analysis-timeout` with the limit named. The
    # slowness is injected at the entry the census calls, because a source file
    # whose *lowering* reliably takes seconds is a moving target across frontend
    # improvements while this test is about the census machinery.
    import time as _time

    from behave.sim.behavior import lowering as lowering_module

    was = lowering_module.lower_with_state_ranges
    try:
        def _stalls(*a, **kw):
            _time.sleep(30)

        lowering_module.lower_with_state_ranges = _stalls
        row = census.census_row(lowers / "behavior.py", timeout_s=1)
    finally:
        lowering_module.lower_with_state_ranges = was
    assert row["outcome"] == "analysis-timeout"
    assert row["code"] == "analysis-timeout"
    assert row["representation_cost"] is None

    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "behavior.py").write_text("this is not python (\n")
    row = census.census_row(broken / "behavior.py")
    assert row["outcome"] == "crashed"
    assert row["code"].startswith("crash-")
    assert row["representation_cost"] is None


def test_representation_cost_percentiles_are_recordable_and_deterministic():
    census = _tool_module("lowering_census")

    values = list(range(1, 101))
    assert census.percentile_summary(reversed(values)) == {
        "count": 100,
        "p50": 50,
        "p90": 90,
        "p95": 95,
        "p99": 99,
        "max": 100,
    }
    assert census.percentile_summary([]) is None


def test_nonlowered_diagnostics_are_derived_complete_json_lines():
    census = _tool_module("lowering_census")
    lowered = {"path": "suite/a", "outcome": "lowered"}
    refused = {
        "path": "suite/b", "outcome": "refused", "code": "unsupported-form",
        "what": "not in the finite subset",
    }
    timed_out = {
        "path": "suite/c", "outcome": "analysis-timeout",
        "code": "analysis-timeout", "what": "budget elapsed",
    }

    import json

    lines = census.nonlowered_diagnostics([lowered, refused, timed_out])
    assert tuple(json.loads(line) for line in lines) == (refused, timed_out)
    assert lines == tuple(sorted(lines, key=lambda line: json.loads(line)["path"]))


def test_the_codes_the_work_list_is_ordered_by_are_pinned():
    """todo3's first-blocker table is a prioritized work list keyed by these
    identities. Renaming one silently breaks every comparison against a dated
    census, so the load-bearing ones are pinned here: a rename must arrive as a
    deliberate edit to this test, beside a note in the census history.
    """
    from behave.core.paths import problems_root
    from behave.sim.symbolic import ir
    from behave.sim.behavior.lowering import LoweringError, lower_with_state_ranges

    expected = {
        # ai_ml_arch_dma_dma_strided_2d was pinned to output-capacity-unproved;
        # the source planner now proves its capacity and it lowers (2026-10-18).
        # The raw lowering entry is deliberately single-domain.  Multi-clock
        # construction goes through the typed task boundary instead of guessing.
        "cvdp_copilot_fifo_async_0001": "step-method-ambiguous",
    }
    by_name = {p.parent.name: p for p in problems_root().rglob("*/behavior.py")
               if p.parent.name in expected}
    assert set(by_name) == set(expected), "the dataset moved under the pins"
    for name, code in sorted(expected.items()):
        try:
            lower_with_state_ranges(by_name[name].read_text(), file="behavior.py")
            raise AssertionError(f"{name} lowered; the pin is stale")
        except LoweringError as exc:
            assert exc.code == code, (name, exc.code, exc.what)

    # Finite computed mapping keys retain their individual value contracts, so
    # this 4x4 tile lowers as one record. Its outputs are the six packed fields
    # its interface declares; the earlier lowering flattened them into 112
    # per-element names. The historical census remains immutable.
    systolic = problems_root() / (
        "ai_ml/ai_ml_arch_systolic_systolic_4x4_os/behavior.py")
    proc = lower_with_state_ranges(
        systolic.read_text(), file="behavior.py")
    assert set(proc.outputs) == {"out_b", "out_c", "out_ids", "out_lanes",
                                 "out_last", "out_propagate"}

    # Fixed slicing of a finite dynamic carrier and the subsequent block
    # rewrites now share one representation plan, so the complete Argon2
    # round lowers instead of stopping at its first slice.
    argon2 = problems_root() / (
        "cryptography/cryptography_sym_memhard_argon2_g_block_mix/behavior.py")
    proc = lower_with_state_ranges(argon2.read_text(), file="behavior.py")
    assert proc.outputs == ["out_next_block"]

    # Retained loops moved finite expansion from lowering into the encoding
    # plan.  This GPTQ model is the population regression for that boundary:
    # its three statement loops and output comprehension now lower, and
    # source-vs-IR audits cover complete
    # 128- and 256-step quantization blocks.  The historical census remains an
    # immutable measurement; the next clean census will record the new row.
    gptq = problems_root() / (
        "ai_ml/ai_ml_quant_gptq_gptq_per_channel_w4/behavior.py")
    proc = lower_with_state_ranges(gptq.read_text(), file="behavior.py")
    # The loops stay retained rather than expanded. Later lowering changes
    # retain more of them (six on 2026-10-18, four when this was written).
    assert sum(isinstance(cmd, ir.For) for cmd in ir.walk(proc.body)) >= 4

    # This model used to stop at a helper comprehension because construction
    # defaults were not carried into its source-value plan.  The same exact
    # facts now bound that comprehension and the retained write loops;
    # replacing those facts by the generic loop ceiling would first exhaust
    # the source-plan budget and make this regression disappear behind a less
    # precise refusal.  The number of loops the planner keeps as loops rather
    # than summarizing is not load-bearing (five when this was written, two on
    # 2026-10-18); lowering at all is.
    paged_kv = problems_root() / (
        "ai_ml/ai_ml_quant_kv_paged_quant_kv_block/behavior.py")
    proc = lower_with_state_ranges(
        paged_kv.read_text(), file="behavior.py")
    assert sum(isinstance(cmd, ir.For) for cmd in ir.walk(proc.body)) >= 1

    # Optional source results are represented by a presence tag rather than
    # treating ``None`` as an unsupported literal.  This population model is
    # stateful across calls and exercises both absent and one-record returns;
    # the source/IR audit covers its frame-start, tie-break, and frame-end
    # paths, while this pin prevents it silently returning to the refusal set.
    streaming_topk = problems_root() / (
        "ai_ml/ai_ml_sample_pick_sample_streaming_topk/behavior.py")
    proc = lower_with_state_ranges(
        streaming_topk.read_text(), file="behavior.py")
    assert proc.outputs == ["out_token_id"]
    assert proc.output_capacity == 1


def test_the_census_header_binds_commit_and_dataset(tmp_path):
    """A dated result under revision A must not certify an improvement claimed at
    revision B, and a census over an edited dataset must not present itself as a
    re-measurement: the digest is over every model's bytes, so either edit changes
    the header.
    """
    census = _tool_module("lowering_census")

    a, b = tmp_path / "a.py", tmp_path / "b.py"
    a.write_text("one\n")
    b.write_text("two\n")

    # Order-independent, content-sensitive.
    first = census.dataset_digest([a, b])
    assert len(first) == 64
    assert census.dataset_digest([b, a]) == first
    b.write_text("three\n")
    assert census.dataset_digest([a, b]) != first


def test_dataset_identity_frames_paths_and_raw_bytes(tmp_path):
    """Equal bytes under different logical paths remain different datasets."""
    census = _tool_module("lowering_census")

    left = tmp_path / "left" / "same.py"
    right = tmp_path / "right" / "same.py"
    left.parent.mkdir()
    right.parent.mkdir()
    left.write_bytes(b"same bytes\x00with a delimiter-like payload")
    right.write_bytes(left.read_bytes())

    assert census.dataset_digest([left]) != census.dataset_digest([right])


def test_census_rows_cover_unique_suite_relative_paths(monkeypatch, tmp_path):
    """Parallel completion order cannot reorder or omit same-named tasks."""
    import pytest

    census = _tool_module("lowering_census")
    data = tmp_path / "data"
    first = data / "problems" / "suite-a" / "same" / "behavior.py"
    second = data / "problems" / "suite-b" / "same" / "behavior.py"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text("one")
    second.write_text("two")
    monkeypatch.setenv("BEHAVE_DATA_DIR", str(data))

    def row(path):
        return {
            "task": "same",
            "path": path,
            "outcome": "lowered",
            "code": None,
            "what": None,
            "site": None,
            "stage": None,
            "ir_commands": 1,
            "representation_cost": 1,
            "elapsed_s": 0.1,
        }

    rows = [row("suite-b/same"), row("suite-a/same")]
    census.validate_rows(rows, [first, second])
    assert [item["path"] for item in sorted(
        rows, key=lambda item: (item["path"], item["task"]))] == [
            "suite-a/same", "suite-b/same"]

    with pytest.raises(SystemExit, match="same task path"):
        census.validate_rows([row("suite-a/same"), row("suite-a/same")],
                             [first, second])
    with pytest.raises(SystemExit, match="do not cover exactly"):
        census.validate_rows([row("suite-a/same")], [first, second])


def test_census_row_rejects_non_exact_deadline_types(tmp_path):
    census = _tool_module("lowering_census")
    path = tmp_path / "task" / "behavior.py"
    path.parent.mkdir()
    path.write_text("class M:\n    def process(self, a):\n        return a\n")

    import pytest

    for value in (True, 1.5, "1"):
        with pytest.raises(ValueError, match="at least 1 second"):
            census.census_row(path, timeout_s=value)


def test_the_deadline_survives_the_lowerings_broad_catches():
    """Review 13 P0. `_Alarm` inherited `Exception`, and the lowering has broad
    `except Exception` regions -- reading a model's types, for one -- so a task that
    stalled inside one returned `outcome="lowered"` after the deadline (6 wall
    seconds under `timeout_s=1`, reproduced). The deadline is `BaseException` now,
    like `measure_authority.Deadline`: application code has no business catching a
    measurement limit, and this pins the stall **inside** the catch region rather
    than at the entry, which the earlier test covered and this hole walked past.
    """
    import pathlib
    import time

    from behave.sim.symbolic import typecheck as types_module

    census = _tool_module("lowering_census")
    assert issubclass(census._Alarm, BaseException)
    assert not issubclass(census._Alarm, Exception)

    task = pathlib.Path(_repo()) / "scratch" / f"census_stall_{time.time_ns()}"
    task.mkdir(parents=True)
    (task / "behavior.py").write_text(
        "class M:\n"
        "    def reset(self):\n"
        "        self.acc = 0\n"
        "    def process(self, a):\n"
        "        self.acc = (self.acc + a) & 0xFF\n"
        "        return {'out': self.acc}\n")
    was = types_module.of_procedure
    try:
        def stalls(proc):
            time.sleep(30)
            return was(proc)

        types_module.of_procedure = stalls
        row = census.census_row(task / "behavior.py", timeout_s=1)
    finally:
        types_module.of_procedure = was
        import shutil

        shutil.rmtree(task, ignore_errors=True)
    assert row["outcome"] == "analysis-timeout", row


def test_a_census_record_cannot_be_overwritten_or_torn(tmp_path):
    """Review 13 P1. The record's name carried only date and commit, so a
    `--match`/`--timeout` run on the same day silently replaced the canonical
    836-row record -- inside the one directory the dirty check deliberately
    ignores -- and a plain `write_text` interrupted mid-run left truncated JSON
    under the canonical name. The name now carries the run identity (dataset,
    timeout, target set), an existing record with different content refuses, the
    write is an exclusive temp-file install, and `--write` with `--match` is refused at
    the parser.
    """
    census = _tool_module("lowering_census")

    full = census.record_path("abc1234", "20260827", "d" * 16, 300, [], 176)
    targeted = census.record_path("abc1234", "20260827", "d" * 16, 10,
                                  ["ai_ml"], 176)
    other_timeout = census.record_path("abc1234", "20260827", "d" * 16, 10, [],
                                       176)
    other_workers = census.record_path("abc1234", "20260827", "d" * 16, 300, [],
                                       8)
    assert len({str(full), str(targeted), str(other_timeout),
                str(other_workers)}) == 4, \
        "dataset, timeout, target set and workers each change the identity"

    where = tmp_path / "record.json"
    document = {"kind": "lowering-census", "total": 1, "rows": [{"task": "t"}]}
    census.write_record(where, document)
    first = where.read_text()
    assert first.endswith("\n") and "lowering-census" in first
    # Idempotent for the identical document; a refusal for a different one.
    census.write_record(where, document)
    import pytest

    with pytest.raises(SystemExit, match="refusing to replace"):
        census.write_record(where, {**document, "total": 2})
    assert where.read_text() == first, "the refused write must not touch the file"
    # No stray temp file survives the atomic path.
    assert [p.name for p in tmp_path.iterdir()] == ["record.json"]


def test_the_census_rows_are_over_the_frozen_snapshot(tmp_path):
    """Review 14 P1. The dataset digest was computed after the last lowering
    finished, so a model edited during a 17-minute run produced rows over a
    mixture of versions under a header binding only the final bytes. The rows
    lower the text the parent froze at the start; the digest is over that
    snapshot; and a caller can hand `census_row` the frozen source directly.
    """
    census = _tool_module("lowering_census")

    task = tmp_path / "m"
    task.mkdir()
    good = ("class M:\n"
            "    def reset(self):\n"
            "        self.acc = 0\n"
            "    def process(self, a):\n"
            "        self.acc = (self.acc + a) & 0xFF\n"
            "        return {'out': self.acc}\n")
    (task / "behavior.py").write_text(good)
    frozen = (task / "behavior.py").read_text()
    # The file changes after the freeze; the row is still about the snapshot.
    (task / "behavior.py").write_text("this is not python (\n")
    row = census.census_row(task / "behavior.py", source=frozen)
    assert row["outcome"] == "lowered", "the row lowered the frozen bytes"
    # And the digest can be taken over the snapshot rather than the disk.
    from_snapshot = census.dataset_digest(
        [task / "behavior.py"],
        contents={str((task / "behavior.py").resolve()): frozen})
    (task / "behavior.py").write_text(good)
    assert from_snapshot == census.dataset_digest([task / "behavior.py"])


def test_the_census_refuses_nonsense_limits(tmp_path):
    """Review 15. `signal.alarm(0)` disables the deadline entirely and `alarm(-1)`
    wraps to about 2**32-1 seconds, so a non-positive limit would not fail -- it
    would silently unmeasure itself. Refused by value, at the row and at the CLI.
    """
    import pytest

    census = _tool_module("lowering_census")

    task = tmp_path / "m"
    task.mkdir()
    (task / "behavior.py").write_text(
        "class M:\n"
        "    def reset(self):\n"
        "        pass\n"
        "    def process(self, a):\n"
        "        return {'out': a}\n")
    for bad in (0, -1):
        with pytest.raises(ValueError, match="at least 1 second"):
            census.census_row(task / "behavior.py", timeout_s=bad)


def test_the_provenance_probe_answers_in_one_shape_everywhere(tmp_path, monkeypatch):
    """Probe a real owned repository, independent of the test checkout's metadata."""
    import inspect
    import subprocess

    census = _tool_module("lowering_census")
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main", str(tmp_path)], check=True)
    subprocess.run([
        "git", "-c", "user.name=BEHAVE test", "-c", "user.email=test@example.invalid",
        "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
        "commit", "--allow-empty", "-qm", "provenance fixture",
    ], cwd=tmp_path, check=True)
    monkeypatch.setattr(census, "__file__", str(tmp_path / "tools" / "lowering_census.py"))

    got = census._commit()
    assert len(got) == 4
    short, full, dirty, detached = got
    assert full.startswith(short) and len(full) == 40
    assert isinstance(dirty, bool)
    assert isinstance(detached, bool)
    assert not dirty and not detached
    (tmp_path / "untracked").write_text("counts as dirty\n")
    assert census._commit()[2:] == (True, False)
    subprocess.run(["git", "switch", "--quiet", "--detach"], cwd=tmp_path, check=True)
    assert census._commit()[2:] == (True, True)
    src = inspect.getsource(census.main)
    assert src.count("_commit()") == 2
    for line in src.splitlines():
        if "_commit()" in line:
            assert line.count(",") == 3, f"a _commit() unpack went stale: {line}"
