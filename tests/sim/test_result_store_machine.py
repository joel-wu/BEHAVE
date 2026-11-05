"""The result store as a state machine: one record per invocation, typed transitions.

Split out of `test_wave1_closure.py` by concern. Every test here is a review
counterexample turned regression: the two-file disagreement, the torn read, the stale
writer, the watchdog race, the terminal latch, the score invariants, the sealed record
type. The shared fixtures stay defined in the wave file and are imported, so there is
one definition of a staged problem and of a real concrete result.
"""
import pathlib  # noqa: F401

import pytest

from behave.sim import api
from behave.sim.core.refusal import Refused

from .test_api_path import (  # noqa: F401
    Deaf,
    Doubling,
    _evaluate,
    _live,
    _ref,
    contract,
    realization,
    schedule,
)
from .test_wave1_closure import HOLD, _concrete, _repo, _staged, _top_of  # noqa: F401


def test_early_refusals_leave_one_consistent_authoritative_record(tmp_path):
    """Three early returns -- both toolchains failing to read the RTL, a port-name
    mismatch, a multi-clock task -- built a report and returned, leaving no
    `authority.json`. A reader then found either nothing or the file a previous attempt
    had left, and neither says what this run concluded."""
    import json

    from behave.sim import result_store

    # The invariant, exercised: the helper every early exit goes through writes the
    # record, and writes it through a rename with no moment in which the path is empty.
    # (The removal that used to be here was the thing creating that moment.)
    where = tmp_path
    # Through the store, like every other exit: the early refusal is not a second writer
    # with its own idea of the record.
    store = result_store.ResultStore(where)
    result = result_store.refused_early(
        store, "authority-toolchain-could-not-read-the-rtl", "nothing ran", detail="x")
    assert result.kind == "refused"
    written = json.loads((where / "authority.json").read_text())
    assert written["why"] == "authority-toolchain-could-not-read-the-rtl"
    assert written["run_invocation"] == store.invocation
    # Written again over itself: still exactly one record in each file, no `.partial`
    # left behind. The store's own cross-process gate sits beside them and is not a
    # record.
    #
    # **Both files, and that is the change.** `decided` used to write `authority.json`
    # alone, so between the decision and the report the two disagreed about what the run
    # concluded -- and an early exit left a directory with a verdict in one file and
    # nothing in the other. They are two views of one record, so a decision is written
    # into both, and the report says the same thing as the authority inside it.
    result_store.refused_early(store, "authority-port-name-mismatch", "nor this")
    assert sorted(p.name for p in where.iterdir()
                  if not p.name.startswith(".")) == ["authority.json",
                                                     "cosim_report.json",
                                                     "latest.json", "runs"]
    a = json.loads((where / "authority.json").read_text())
    r = json.loads((where / "cosim_report.json").read_text())
    assert r["result_kind"] == a["kind"] == "refused"
    assert r["run_invocation"] == a["run_invocation"] == store.invocation
    # And the one authoritative document the two are views of: `latest.json` names it,
    # it belongs to this invocation, and it holds both halves together.
    pointer = json.loads((where / "latest.json").read_text())
    assert pointer["run_invocation"] == store.invocation
    record = json.loads((where / pointer["result"]).read_text())
    assert record["authority"]["kind"] == "refused"
    assert record["report"]["result_kind"] == "refused"
    assert record["run_sequence"] == a["run_sequence"] == r["run_sequence"]
    assert a["derived_from"] == pointer["result"]


def test_a_report_may_not_disagree_with_the_authority_inside_it():
    """A report holds a copy of the authoritative record and its own `result_kind`
    beside it: two statements of one fact, assembled at different places in a long
    function. Where they disagree the run stops, because that is this code contradicting
    itself rather than anything about a design."""
    import os
    import shutil

    from behave.sim.result_store import ResultStore

    where = _repo() / "scratch" / f"consistency_{os.getpid()}"
    shutil.rmtree(where, ignore_errors=True)
    where.mkdir(parents=True)
    store = ResultStore(where)

    with pytest.raises(ValueError, match="concrete_evaluation"):
        store.commit(report={"result_kind": "concrete_evaluation",
                             "authority": {"kind": "refused"}})
    with pytest.raises(ValueError, match="another process"):
        store.commit(report={"result_kind": "refused",
                             "authority": {"kind": "refused",
                                           "invocation": "0" * 32}})
    with pytest.raises(ValueError, match="authority/1"):
        store.commit(report={"result_kind": "refused",
                             "authority": {"kind": "refused",
                                           "version": "authority/1"}})
    # Agreeing halves are written.
    assert store.commit(report={"result_kind": "refused",
                                "authority": {"kind": "refused"}}) is True
    shutil.rmtree(where, ignore_errors=True)


def test_one_store_writes_this_invocation_s_two_files():
    """Nine call sites wrote `authority.json` or `cosim_report.json`, each with its own
    idea of a complete record: an early refusal wrote one and returned, the watchdog wrote
    the other from a snapshot, the final report wrote outside the lock the decided score
    had been written under. Every exit goes through one store now, and these are the three
    properties that buys.
    """
    import json
    import os
    import shutil
    import threading

    from behave.sim.authority import PARTIAL, Authoritative
    from behave.sim.result_store import ResultStore, partial_after_timeout

    where = _repo() / "scratch" / f"store_{os.getpid()}"
    shutil.rmtree(where, ignore_errors=True)
    where.mkdir(parents=True)

    # A leftover from a previous attempt, which a reader could not tell from this run's.
    (where / "authority.json").write_text(json.dumps(
        {"kind": "concrete_evaluation", "passed": True, "why": "last week"}))

    store = ResultStore(where, progress={"result_kind": PARTIAL, "partial": False})

    # ── one invocation: both files carry it, and `begin` replaces the leftover ──
    store.begin()
    authority = json.loads((where / "authority.json").read_text())
    report = json.loads((where / "cosim_report.json").read_text())
    assert authority["run_invocation"] == store.invocation \
        == report["run_invocation"]
    assert authority["kind"] == "refused" and "passed" not in authority, \
        "a run that has not finished carries no score, least of all last week's"

    # ── one writer: a caller that cannot take the lock writes nothing ──
    #
    # **From another thread.** The store's lock is reentrant, because a transition takes
    # it and then calls `commit`, which takes it again -- one thread, one transaction,
    # two frames. What it excludes is a *second* thread, which is what the watchdog is.
    held, release = threading.Event(), threading.Event()

    def _holds_it():
        with store._lock:
            held.set()
            release.wait(10)

    holder = threading.Thread(target=_holds_it, daemon=True)
    holder.start()
    assert held.wait(10)
    assert store.commit(report={"result_kind": "partial"}, timeout=0.05) is False
    release.set()
    holder.join(10)
    assert json.loads((where / "cosim_report.json").read_text())["in_progress"] is True

    # ── one decision: the file and this run's own state together ──
    store.decided(Authoritative(PARTIAL, why="something the run established"))
    assert json.loads((where / "authority.json").read_text())["kind"] == PARTIAL
    got = store.snapshot()
    assert got.progress["score_decided_before_any_timeout"] is True
    assert got.progress["run_invocation"] == store.invocation
    assert got.terminal is True, "a decided result is terminal, and a kill may not undo it"
    assert partial_after_timeout(dict(got.progress)) is False

    # ── the state is the store's: the dictionary handed in is not written through ──
    caller_side = {"result_kind": PARTIAL}
    other = ResultStore(where / "second", progress=caller_side)
    other.record(phase_reached="solve")
    assert "phase_reached" not in caller_side, \
        "the caller still shares mutable state with the store"
    assert other.snapshot().progress["phase_reached"] == "solve"

    shutil.rmtree(where, ignore_errors=True)


def test_two_processes_writing_one_problem_directory_do_not_mix():
    """The store's lock was a `threading.Lock`, which two processes do not share -- and a
    sweep that runs one task twice, or two sweeps over one dataset, are two processes over
    one directory. Each held its own lock, neither saw the other, and the pair of files
    could end up a mixture of two runs.

    Two children writing, a third reading throughout: every record a reader sees must
    belong to exactly one of them and parse whole.
    """
    import json
    import multiprocessing as mp
    import os
    import shutil

    from behave.sim.result_store import ResultStore

    where = _repo() / "scratch" / f"twoproc_{os.getpid()}"
    shutil.rmtree(where, ignore_errors=True)
    where.mkdir(parents=True)

    def writer(tag, rounds):
        # One store per round: a verdict is one transition per invocation now, so a
        # writer that keeps deciding is many invocations -- which is also what the sweep
        # this test models actually does. The filler keeps the payload big enough for a
        # torn write to be observable.
        from behave.sim.result_store import refused_early

        for n in range(rounds):
            store = ResultStore(where)
            store.begin()
            refused_early(store, f"{tag}-{n}", "a writer of this test",
                          filler="x" * 20000)

    observations = where / "observations.json"

    def reader(stop):
        seen = []
        while not stop.is_set():
            for name in ("authority.json", "cosim_report.json"):
                try:
                    got = json.loads((where / name).read_text())
                except (OSError, ValueError) as exc:
                    seen.append((name, "unreadable", type(exc).__name__))
                    continue
                seen.append((name, got.get("run_invocation", "?"),
                             str(got.get("why", "?")).split("-")[0]))
        observations.write_text(json.dumps(seen))

    ctx = mp.get_context("fork")
    stop = ctx.Event()
    watching = ctx.Process(target=reader, args=(stop,))
    watching.start()
    writers = [ctx.Process(target=writer, args=(tag, 25)) for tag in ("A", "B")]
    for one in writers:
        one.start()
    for one in writers:
        one.join()
        assert one.exitcode == 0
    stop.set()
    watching.join()
    assert watching.exitcode == 0
    rows = json.loads(observations.read_text())

    live = []
    for name in ("authority.json", "cosim_report.json"):
        mine = [r for r in rows if r[0] == name]
        first = next((i for i, r in enumerate(mine) if r[1] != "unreadable"), len(mine))
        live += mine[first:]
    assert live, "the reader saw nothing"
    assert not [r for r in live if r[1] == "unreadable"], \
        [r for r in live if r[1] == "unreadable"][:3]
    owners = {}
    for _, invocation, tag in live:
        if tag not in ("A", "B"):
            continue                      # a `begin` placeholder, owned by nobody yet
        assert owners.setdefault(invocation, tag) == tag, \
            f"invocation {invocation[:8]} carried both writers"
    # What is asserted is the store's property -- no torn read, no record mixing two
    # writers -- and not the scheduler's: whether the sampling thread happened to catch
    # both writers mid-flight is timing, and a full machine can serialise them.
    assert owners and set(owners.values()) <= {"A", "B"}, owners

    shutil.rmtree(where, ignore_errors=True)


def test_the_watchdog_and_the_decision_interleave_without_mixing_the_record():
    """T3-15's requirement, as a race rather than as an ordering.

    The two writers a run has are the main thread and the watchdog, and the previous
    version let them interleave inside one record: the kill copied the shared dictionary,
    the main thread decided, and the kill's commit wrote its copy with the decision
    already in it -- so the pair of files could end up one run's fields with the other's
    flags on top, and a reader could not tell which.

    Run them against each other, many times, with a reader watching throughout. The
    verdict must survive: whichever order the two threads reach the store in, a decided
    result is never replaced by a partial one.

    **And what a lock-free reader is owed, which is not what this first asserted.** One
    commit writes two files and POSIX renames one path at a time, so a reader that does
    not take the lock can catch the pair mid-commit -- measured here, 6 of 12 rounds, as
    `report says refused, authority says concrete_evaluation`. Two files cannot be made
    atomic for such a reader, so the guarantee is the one that can be kept: every
    disagreement is *visibly* a straddled commit, because the two records carry different
    `run_sequence` values. `result_store.read_record` reads the single current
    record, or rejects an inconsistent legacy pair without an internal retry. A
    disagreement at one sequence number would be the record contradicting itself; a
    disagreement across two is a reader that read too early.
    """
    import json
    import os
    import shutil
    import threading

    from behave.sim import result_store
    from behave.sim.authority import CONCRETE, PARTIAL
    from behave.sim.result_store import ResultStore

    root = _repo() / "scratch" / f"race_{os.getpid()}"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)

    torn = []
    try:
        for round_ in range(12):
            where = root / f"r{round_}"
            store = ResultStore(where, progress={"result_kind": PARTIAL})
            store.begin()

            go = threading.Event()
            states = {}
            verdict = _concrete()

            def decide(go=go, states=states, store=store, verdict=verdict):
                go.wait(10)
                states["decide"] = store.decided(verdict)

            def kill(go=go, states=states, store=store):
                go.wait(10)
                states["kill"] = store.timeout_if_undecided(
                    store.invocation, {"total_elapsed_s": 1.0}, timeout=10.0)

            def read(go=go, where=where, round_=round_):
                go.wait(10)
                for _ in range(300):
                    # The raw pair, taken without the lock and without the protocol.
                    try:
                        a = json.loads((where / "authority.json").read_text())
                        r = json.loads((where / "cosim_report.json").read_text())
                    except (OSError, ValueError) as exc:
                        torn.append(f"round {round_}: {type(exc).__name__}")
                        return
                    if a["run_invocation"] != r["run_invocation"]:
                        torn.append(f"round {round_}: two invocations in one record")
                        return
                    if r.get("result_kind") != a["kind"] \
                            and a["run_sequence"] == r["run_sequence"]:
                        torn.append(
                            f"round {round_}: at sequence {a['run_sequence']} the "
                            f"report says {r.get('result_kind')} and the authority "
                            f"says {a['kind']}")
                        return
                    # And through the protocol, which is what a consumer uses: a pair it
                    # returns at all is a pair from one commit.
                    got = result_store.read_record(where)
                    if got is not None and got[1].get("result_kind") != got[0]["kind"]:
                        torn.append(f"round {round_}: read_record returned a pair that "
                                    f"disagrees")
                        return

            threads = [threading.Thread(target=fn, daemon=True)
                       for fn in (decide, kill, read)]
            for t in threads:
                t.start()
            go.set()
            for t in threads:
                t.join(20)

            # Whichever order they reached the store in, exactly one of them decided
            # and the other became telemetry beside it -- terminal latches in both
            # directions now, because a timeout is the last thing a dying invocation
            # writes. The record is internally consistent either way.
            after = json.loads((where / "authority.json").read_text())
            report = json.loads((where / "cosim_report.json").read_text())
            assert report["result_kind"] == after["kind"], (round_, states)
            record = json.loads(
                (where / "runs" / store.invocation / "result.json").read_text())
            assert record["terminal"] is True
            if after["kind"] == CONCRETE:
                # The decision won; the kill annotated it.
                assert states["kill"][1] == "decided", states
            else:
                # The kill won; the decision arrived at a dead invocation and is
                # recorded beside the verdict rather than replacing it.
                assert after["kind"] == PARTIAL, (round_, after["kind"])
                assert states["decide"] is False, states
                later = record["report"]["later_decisions"]
                assert [d["kind"] for d in later] == [CONCRETE]
        assert not torn, torn[:3]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_kill_after_the_verdict_does_not_take_the_verdict_away():
    """The regression the transition exists to refuse.

    The watchdog took `dict(_progress)`, merged its timeout flags into that copy, and
    committed the result. Between the snapshot and the commit the main thread could reach
    a verdict -- and the kill then wrote a `partial` record assembled before that verdict
    existed, straight over it. Every task whose telemetry ran long came back interrupted
    with its score computed and discarded.

    Reproduced here in the order that matters: snapshot, decide, kill.
    """
    import json
    import os
    import shutil
    import threading

    from behave.sim.authority import CONCRETE, PARTIAL
    from behave.sim.result_store import ResultStore

    where = _repo() / "scratch" / f"kill_{os.getpid()}_{threading.get_ident()}"
    shutil.rmtree(where, ignore_errors=True)
    where.mkdir(parents=True)
    try:
        store = ResultStore(where, progress={"result_kind": PARTIAL})
        store.begin()

        # The watchdog's view of the run, taken before the main thread decides.
        stale = store.snapshot()
        assert stale.progress["result_kind"] == PARTIAL

        store.decided(_concrete(why="the design was compared"))
        assert json.loads((where / "authority.json").read_text())["kind"] == CONCRETE

        # The kill, now, holding that stale view. It does not get to write it.
        wrote, state = store.timeout_if_undecided(
            store.invocation, {"total_elapsed_s": 900.0}, timeout=5.0)
        assert wrote is True and state == "decided", (wrote, state)
        after = json.loads((where / "authority.json").read_text())
        assert after["kind"] == CONCRETE, "the kill overwrote a decided verdict"
        report = json.loads((where / "cosim_report.json").read_text())
        assert report["result_kind"] == CONCRETE
        assert report["hard_timeout"] is True and report["partial"] is False
        assert report["interrupted_after_the_result"] is True

        # ── a run with no verdict is still cut short, and says so in both files ──
        second = ResultStore(where / "b", progress={"result_kind": PARTIAL})
        second.begin()
        wrote, state = second.timeout_if_undecided(
            second.invocation, {"hard_timeout_phase": "solve"}, timeout=5.0)
        assert wrote is True and state == "partial"
        report = json.loads((where / "b" / "cosim_report.json").read_text())
        assert report["result_kind"] == PARTIAL
        assert report["authority"]["kind"] == PARTIAL
        assert report["authority"]["authoritative"] is False
        assert report["partial"] is True

        # ── a caller holding another run's identity does not write this one ──
        with pytest.raises(ValueError, match="names run"):
            second.timeout_if_undecided("0" * 32, {}, timeout=5.0)
        with pytest.raises(ValueError, match="does not write another"):
            second.commit(report={"result_kind": PARTIAL}, invocation="0" * 32)

        # ── and a sequence number that only goes forward, across both files ──
        a = json.loads((where / "b" / "authority.json").read_text())["run_sequence"]
        r = json.loads((where / "b" / "cosim_report.json").read_text())["run_sequence"]
        assert r >= a >= 1
    finally:
        shutil.rmtree(where, ignore_errors=True)


def test_a_stale_invocation_does_not_overwrite_a_newer_run_s_verdict():
    """`flock` orders writers that overlap. It says nothing about one that is late.

    Reproduced: an older invocation opened the directory, a newer one started and decided
    `concrete_evaluation`, and the older one's watchdog then took the lock legitimately
    and wrote `partial` over it.

    Each run writes its own `runs/<invocation>/result.json`, which is the authoritative
    document; the shared views and `latest.json` are only advanced by a run that is not
    older than the one they already name.
    """
    import json
    import os
    import shutil

    from behave.sim.authority import CONCRETE, PARTIAL
    from behave.sim.result_store import ResultStore, read_record

    where = _repo() / "scratch" / f"stale_{os.getpid()}"
    shutil.rmtree(where, ignore_errors=True)
    where.mkdir(parents=True)

    try:
        old = ResultStore(where)
        old.begin()
        new = ResultStore(where)
        new.begin()
        new.decided(_concrete())
        assert json.loads((where / "authority.json").read_text())["kind"] == CONCRETE

        wrote, state = old.timeout_if_undecided(
            old.invocation, {"total_elapsed_s": 1.0}, timeout=5.0)
        assert wrote is True and state == "partial", (wrote, state)

        # The older run's own record says what happened to it, in its own directory.
        older = json.loads(
            (where / "runs" / old.invocation / "result.json").read_text())
        assert older["authority"]["kind"] == PARTIAL
        # And the shared view still names the newer run.
        assert json.loads((where / "authority.json").read_text())["kind"] == CONCRETE
        pointer = json.loads((where / "latest.json").read_text())
        assert pointer["run_invocation"] == new.invocation
        authority, report = read_record(where)
        assert authority["kind"] == CONCRETE and report["result_kind"] == CONCRETE
    finally:
        shutil.rmtree(where, ignore_errors=True)


def test_shared_views_preserve_distinct_submillisecond_start_order(tmp_path, monkeypatch):
    import json
    import uuid

    from behave.sim import result_store
    from behave.sim.authority import CONCRETE, PARTIAL

    def opened(at, identity):
        with monkeypatch.context() as patch:
            patch.setattr(result_store.time, "time", lambda: at)
            patch.setattr(result_store.uuid, "uuid4", lambda: uuid.UUID(hex=identity))
            return result_store.ResultStore(tmp_path)

    old = opened(1000.0001, "f" * 32)
    new = opened(1000.0002, "0" * 31 + "1")
    assert old.started < new.started and round(old.started, 3) == round(new.started, 3)
    old.begin()
    new.begin()
    new.decided(_concrete())
    wrote, state = old.timeout_if_undecided(old.invocation, {}, timeout=5.0)
    assert wrote and state == PARTIAL

    pointer = json.loads((tmp_path / "latest.json").read_text())
    assert pointer["run_invocation"] == new.invocation
    assert pointer["run_started_at"] == new.started
    for name in ("authority.json", "cosim_report.json", pointer["result"]):
        document = json.loads((tmp_path / name).read_text())
        assert document["run_invocation"] == new.invocation
        assert document["run_started_at"] == new.started
    authority, report = result_store.read_record(tmp_path)
    assert authority["kind"] == report["result_kind"] == CONCRETE
    older = json.loads((tmp_path / "runs" / old.invocation / "result.json").read_text())
    assert older["authority"]["kind"] == PARTIAL
    assert older["run_started_at"] == old.started


@pytest.mark.parametrize("pointer", ['{"result":"missing.json"}', '{}', '{'])
def test_read_record_never_falls_back_from_a_current_owner(tmp_path, pointer):
    """An incomplete new record cannot make an old score authoritative again."""
    import json

    from behave.sim.result_store import read_record

    old = {"run_invocation": "old", "run_sequence": 1}
    for name in ("authority.json", "cosim_report.json"):
        (tmp_path / name).write_text(json.dumps(old))
    (tmp_path / "latest.json").write_text(pointer)
    assert read_record(tmp_path) is None


@pytest.mark.parametrize("matching", [False, True])
def test_read_record_reads_legacy_views_once(tmp_path, monkeypatch, matching):
    from behave.sim import result_store

    authority = {"run_invocation": "old", "run_sequence": 1}
    report = {"run_invocation": "old", "run_sequence": 1 if matching else 2}
    views = {"authority.json": authority, "cosim_report.json": report}
    reads = []

    def read_json(path):
        reads.append(path.name)
        return views.get(path.name)

    monkeypatch.setattr(result_store, "_read_json", read_json)
    assert result_store.read_record(tmp_path) == ((authority, report) if matching else None)
    assert reads == ["latest.json", "authority.json", "cosim_report.json"]


def test_one_commit_may_not_write_two_versions_of_one_record():
    """The two files were separately authoritative and separately assembled.

    Reproduced twice, one review apart: first `authority.json` saying `why = A` while the
    report's copy said `why = B`; then, with that closed for the arguments of one call, a
    hand-built authority dictionary with no version, no invocation and no evaluation
    committed as a terminal concrete result -- the machine checked fields only where they
    were present. There is no public spelling of either any more: `commit` takes no
    authority and no terminal, `decided` takes the `Authoritative` itself, and the
    envelope is immutable all the way down.
    """
    import os
    import shutil

    from behave.sim.authority import PARTIAL
    from behave.sim.result_store import ResultStore

    where = _repo() / "scratch" / f"onerecord_{os.getpid()}"
    shutil.rmtree(where, ignore_errors=True)
    where.mkdir(parents=True)
    try:
        store = ResultStore(where)
        store.begin()
        # No public door for a verdict-as-dictionary, in either parameter.
        with pytest.raises(TypeError):
            store.commit(authority={"kind": PARTIAL, "why": "A"},
                         report={"result_kind": PARTIAL,
                                 "authority": {"kind": PARTIAL, "why": "B"}})
        with pytest.raises(TypeError):
            store.commit(report={"result_kind": PARTIAL}, terminal=True)
        # A report that contradicts itself is refused even with no verdict standing.
        with pytest.raises(ValueError, match="record inside it"):
            store.commit(report={"result_kind": PARTIAL,
                                 "authority": {"kind": "refused"}})

        # Immutable all the way down: neither the record nor a nested value.
        env = store.snapshot()
        with pytest.raises(TypeError):
            env.authority["kind"] = "edited"
        with pytest.raises(TypeError):
            env.report["authority"]["kind"] = "edited"
        with pytest.raises(AttributeError):
            env.sequence = 99
    finally:
        shutil.rmtree(where, ignore_errors=True)


def test_run_envelope_is_a_detached_exact_finite_record_value():
    """The envelope and a stimulus share one closed carrier, all the way down.

    The old freeze stopped at mappings and lists.  A mapping subclass could therefore
    run caller-owned methods while being copied, and an unsupported mutable value could
    survive inside the supposedly immutable snapshot.  Valid records retain their
    established public JSON shape.
    """
    import json

    from behave.sim.result_store import RunEnvelope

    live = {"nested": [{"flag": True, "count": 1}]}
    envelope = RunEnvelope("invocation", 1.25, progress=live)
    live["nested"][0]["flag"] = False
    live["nested"].append({"count": 2})

    expected = {
        "record_version": "run-record/3",
        "run_invocation": "invocation",
        "run_started_at": 1.25,
        "run_sequence": 0,
        "terminal": False,
        "authority": None,
        "report": None,
        "progress": {"nested": [{"count": 1, "flag": True}]},
    }
    assert envelope.as_record() == expected
    assert json.loads(json.dumps(envelope.as_record())) == expected

    nested = envelope.progress["nested"]
    with pytest.raises(TypeError):
        nested[0] = None
    with pytest.raises(TypeError):
        nested[0]["flag"] = False

    # The ordinary wire image reconstructs the same record without carrying aliases
    # back across the boundary.
    wire = envelope.as_record()
    restored = RunEnvelope(
        wire["run_invocation"], wire["run_started_at"],
        sequence=wire["run_sequence"], authority=wire["authority"],
        report=wire["report"], progress=wire["progress"],
        terminal=wire["terminal"])
    wire["progress"]["nested"][0]["count"] = 99
    assert restored.as_record() == expected

    # Freezing is not permission to rewrite the public JSON document.  Canonical digest
    # callers sort explicitly; an envelope keeps the established key order on disk.
    ordered = RunEnvelope("invocation", 1.25, progress={"z": 0, "a": 1})
    assert list(ordered.as_record()["progress"]) == ["z", "a"]


def test_record_value_equality_does_not_treat_mapping_order_as_semantics():
    """Wire presentation order is retained, but cannot create a second value."""

    from behave.sim.core.record_value import (
        freeze_record_value,
        same_record_value,
    )

    left = freeze_record_value({"first": 1, "second": [True]})
    right = freeze_record_value({"second": [True], "first": 1})
    assert list(left) != list(right), "the public insertion order is retained"
    assert same_record_value(left, right)


def test_run_envelope_rejects_scalar_coercions_and_python_subclasses():
    from types import MappingProxyType

    from behave.sim.result_store import RunEnvelope

    class Text(str):
        pass

    class Number(int):
        pass

    class Mapping(dict):
        pass

    class Sequence(list):
        pass

    class ActiveMapping(dict):
        def items(self):
            raise AssertionError("the boundary invoked a caller-owned mapping")

    for kwargs in (
        {"invocation": 7, "started": 1.0},
        {"invocation": Text("run"), "started": 1.0},
        {"invocation": "run", "started": 1},
        {"invocation": "run", "started": 1.0, "sequence": True},
        {"invocation": "run", "started": 1.0, "sequence": Number(1)},
        {"invocation": "run", "started": 1.0, "terminal": 1},
        {"invocation": "run", "started": 1.0,
         "progress": Mapping(value=1)},
        {"invocation": "run", "started": 1.0,
         "progress": {"value": Number(1)}},
        {"invocation": "run", "started": 1.0,
         "progress": {"value": Sequence([1])}},
        {"invocation": "run", "started": 1.0,
         "progress": MappingProxyType(ActiveMapping(value=1))},
    ):
        with pytest.raises((TypeError, ValueError)):
            RunEnvelope(**kwargs)


@pytest.mark.parametrize("bad", [
    {"value": {1, 2}},
    {"value": object()},
    {"value": float("nan")},
    {"value": float("inf")},
])
def test_run_envelope_rejects_values_outside_the_shared_carrier(bad):
    from behave.sim.result_store import RunEnvelope

    with pytest.raises((TypeError, ValueError), match="finite|non-finite"):
        RunEnvelope("run", 1.0, progress=bad)

    cyclic = []
    cyclic.append(cyclic)
    with pytest.raises(ValueError, match="cycle"):
        RunEnvelope("run", 1.0, progress={"value": cyclic})


def test_the_store_is_a_state_machine_and_a_decision_latches():
    """Four rules, over the whole next envelope rather than over one call's arguments.

    Both reproduced against the version that checked only what a call passed:
    `decided(A)` then `decided(B)` left `terminal = True` with the authority changed to
    B, and `decided(A)` then `commit(report=B)` left one record holding `authority = A`
    beside `report.authority = B`.

    The production path reaches the first: the admission gate decides a refusal, and with
    `smoke_test=True` the run continues, so a later toolchain failure reaches
    `refused_early` and `decided` again. That second one is telemetry beside the gate's
    refusal, and this checks it is recorded rather than dropped or raised.
    """
    import json
    import os
    import shutil

    from behave.sim.authority import CONCRETE, PARTIAL, REFUSED, Authoritative
    from behave.sim.result_store import ResultStore

    where = _repo() / "scratch" / f"machine_{os.getpid()}"
    shutil.rmtree(where, ignore_errors=True)
    where.mkdir(parents=True)

    try:
        store = ResultStore(where)
        store.begin()
        first = _concrete()
        assert store.decided(first) is True

        # (1) an identical decision is nothing to do, and says so. The same evaluation,
        # because "identical" means the record, and a stub that serialises equal is not
        # a record.
        assert store.decided(_concrete(evaluation=first.evaluation)) is True
        record = json.loads(store.result_path.read_text())
        assert record["authority"]["kind"] == CONCRETE
        assert "later_decisions" not in (record["report"] or {})

        # (2) a different one is telemetry beside it, not a replacement, and not a crash.
        assert store.decided(Authoritative(REFUSED, why="a later toolchain failure")) \
            is False
        record = json.loads(store.result_path.read_text())
        assert record["terminal"] is True
        assert record["authority"]["kind"] == CONCRETE, "the decision did not latch"
        assert record["authority"]["why"] == "compared"
        later = record["report"]["later_decisions"]
        assert [r["why"] for r in later] == ["a later toolchain failure"]

        # (3) a report that disagrees with the standing record is refused, even though
        # this call passes only the report.
        with pytest.raises(ValueError, match="this report says"):
            store.commit(report={"result_kind": PARTIAL,
                                 "authority": {"kind": PARTIAL, "why": "B"}})
        # And one that quietly *omits* the verdict does not replace the report that
        # carries it: absence is not agreement.
        with pytest.raises(ValueError, match="this report says"):
            store.commit(report={"note": "nothing here says what the run concluded"})

        # (4) there is no way to spell "un-decide" or "here is my own authority": the
        # verdict-bearing states are reached only through the named transitions.
        with pytest.raises(TypeError):
            store.commit(terminal=False)
        with pytest.raises(TypeError):
            store.commit(authority={"kind": CONCRETE, "passed": True})
        with pytest.raises(TypeError):
            store.decided({"kind": CONCRETE, "authoritative": True, "passed": True})

        # The phase fields are in the record, which is what the timeout path collects.
        store.record(phase_reached="smoke")
        store.commit(progress={"phase_reached": "smoke"})
        record = json.loads(store.result_path.read_text())
        assert record["progress"]["phase_reached"] == "smoke"

        # And the progress copies the watchdog reads are not a second authority.
        with pytest.raises(ValueError, match="not a second authority"):
            store.record(result_kind=PARTIAL)
    finally:
        shutil.rmtree(where, ignore_errors=True)


def test_the_store_refuses_a_record_nothing_produced():
    """The machine is fail-closed: a terminal record is refused for what it is missing.

    Reproduced against the checked-where-present version: a hand-built
    `{"kind": "concrete_evaluation", "authoritative": True, "passed": True}` -- no
    version, no invocation, no evaluation -- satisfied every conditional check and
    became a terminal concrete result. Two doors, both closed: the public interface has
    no way to spell a verdict as a dictionary (`decided` takes the `Authoritative`
    itself, `commit` takes no authority and no terminal), and the primitive underneath
    refuses an incomplete terminal record even from an internal caller.
    """
    import os
    import shutil

    from behave.sim.authority import PARTIAL, Authoritative
    from behave.sim.result_store import ResultStore

    where = _repo() / "scratch" / f"failopen_{os.getpid()}"
    shutil.rmtree(where, ignore_errors=True)
    where.mkdir(parents=True)
    try:
        store = ResultStore(where)
        store.begin()
        fake = {"kind": "concrete_evaluation", "authoritative": True, "passed": True}

        # The public doors.
        with pytest.raises(TypeError):
            store.commit(authority=fake, terminal=True)
        with pytest.raises(TypeError):
            store.decided(fake)

        # The primitive, for an internal caller that misbuilds: incomplete is refused,
        # field by named field.
        with pytest.raises(ValueError, match="build version"):
            store._commit(authority=fake,
                          report={"result_kind": fake["kind"], "authority": fake},
                          terminal=True)
        stamped = Authoritative(PARTIAL, why="x").as_dict()
        broken = dict(stamped)
        broken["authoritative"] = True
        with pytest.raises(ValueError, match="is not authoritative"):
            store._commit(authority=broken,
                          report={"result_kind": broken["kind"],
                                  "authority": broken}, terminal=True)
        unnamed = dict(stamped)
        unnamed["kind"] = "something-else"
        with pytest.raises(ValueError, match="names a kind this build has"):
            store._commit(authority=unnamed,
                          report={"result_kind": "something-else",
                                  "authority": unnamed}, terminal=True)

        # And a timeout is terminal, complete, and latched: the invocation is about to
        # die, and nothing legitimate writes after its own death.
        import json

        wrote, state = store.timeout_if_undecided(store.invocation, {}, timeout=5.0)
        assert (wrote, state) == (True, "partial")
        record = json.loads(store.result_path.read_text())
        assert record["terminal"] is True
        assert record["authority"]["version"], "the cut-short record is unstamped"
        with pytest.raises(ValueError, match="already decided"):
            store._commit(authority=fake,
                          report={"result_kind": fake["kind"], "authority": fake},
                          terminal=True)
    finally:
        shutil.rmtree(where, ignore_errors=True)


def test_the_score_appears_exactly_where_there_is_one_and_it_is_the_record_s():
    """The top level of the report is what the consumers actually read.

    `result_kind` and the authority copy were compared and `passed` beside them was not
    -- and `sweep_dataset`, `measure_authority` and `verdict_line` all read the top
    level. Reproduced: a real concrete pass committed with `report["passed"] = False`
    was accepted whole, and the record said PASS in its authority while every consumer
    printed FAIL. The other direction too: a `partial` record took a `passed = True`.

    And the primitive is not a way around the machine: driven straight to a terminal
    state with no `begin` and no report, it used to write a verdict with no account of
    the run behind it.
    """
    import json
    import os
    import shutil

    from behave.sim.authority import CONCRETE, PARTIAL, Authoritative
    from behave.sim.result_store import ResultStore, verdict_line

    where = _repo() / "scratch" / f"score_{os.getpid()}"
    shutil.rmtree(where, ignore_errors=True)
    where.mkdir(parents=True)

    try:
        store = ResultStore(where)
        store.begin()
        store.decided(_concrete())
        standing_summary = json.loads(store.result_path.read_text())["report"][
            "authority"]

        # A concrete report carries the record's score -- not another one, not none.
        with pytest.raises(ValueError, match="record's score"):
            store.commit(report={"result_kind": CONCRETE,
                                 "authority": standing_summary,
                                 "passed": False})
        with pytest.raises(ValueError, match="record's score"):
            store.commit(report={"result_kind": CONCRETE,
                                 "authority": standing_summary,
                                 "passed": 1})
        with pytest.raises(ValueError, match="record's score"):
            store.commit(report={"result_kind": CONCRETE,
                                 "authority": standing_summary})
        assert store.commit(report={"result_kind": CONCRETE,
                                    "authority": standing_summary,
                                    "passed": True, "note": "telemetry"}) is True
        record = json.loads(store.result_path.read_text())
        assert record["authority"]["passed"] is True
        assert record["report"]["passed"] is True
        assert verdict_line(record["report"]) == "PASS"

        # A non-concrete record carries no score at all, in either namespace.
        second = ResultStore(where / "b")
        second.begin()
        assert second.timeout_if_undecided(second.invocation, {}, timeout=5.0) \
            == (True, "partial")
        partial_report = json.loads(second.result_path.read_text())["report"]
        with pytest.raises(ValueError, match="only a concrete evaluation"):
            second.commit(report={**partial_report, "passed": True})
        with pytest.raises(ValueError, match="only a concrete evaluation"):
            second.record(passed=True)

        # And terminal means whole: a verdict with no report is refused even through
        # the private primitive.
        third = ResultStore(where / "c")
        with pytest.raises(ValueError, match="no report is not an account"):
            third._commit(authority=Authoritative(PARTIAL, why="x").as_dict(),
                          terminal=True)
    finally:
        shutil.rmtree(where, ignore_errors=True)


def test_the_claim_record_type_is_sealed():
    """A subclass inherits the checks and overrides the answers.

    The constructor validates the evidence and `Frozen` stops later edits -- and a
    normally initialised subclass over a **genuinely failing** evaluation persisted
    `passed: True`, by overriding `passed` and `as_dict`. The store's `isinstance` took
    it, and `_admits` only compares the record against itself, so the lie was
    internally consistent. Sealed at class creation, and the store checks the exact
    type as the second lock.
    """
    from behave.sim.authority import Authoritative

    with pytest.raises(TypeError, match="sealed"):
        class Lying(Authoritative):
            @property
            def passed(self):
                return True

    # And a genuinely failing evaluation stays a failure through the real type: the
    # shape the subclass would have rewritten, end to end.
    import json
    import os
    import shutil

    from behave.sim.result_store import ResultStore

    class Wrong(Doubling):
        def settle(self, presented, controls):
            got = super().settle(presented, controls)
            if got.get("out_y"):
                got = dict(got, out_y=(got["out_y"] + 1) & 0xFF)
            return got

    task = api.admit(contract(), realization(), reference=_ref())
    failing = _evaluate(task, [schedule([3])], Wrong(), reference=_ref())
    assert failing.passed is False and failing.mismatches

    where = _repo() / "scratch" / f"sealed_{os.getpid()}"
    shutil.rmtree(where, ignore_errors=True)
    where.mkdir(parents=True)
    try:
        store = ResultStore(where)
        store.begin()
        assert store.decided(_concrete(evaluation=failing)) is True
        record = json.loads(store.result_path.read_text())
        assert record["authority"]["passed"] is False
        assert record["report"]["passed"] is False
    finally:
        shutil.rmtree(where, ignore_errors=True)


def test_a_concrete_result_is_a_tagged_union_not_a_label():
    """`Authoritative(CONCRETE, evaluation=anything)` was the store's fake-dict hole
    wrapped one layer deeper: the kind was checked and the evidence was not, so an
    object with a `passed` attribute and an `as_dict` became a terminal concrete PASS
    through the public transitions.

    Closed at both layers. The constructor admits only the exact `ConcreteEvaluation`
    type under CONCRETE -- non-empty, and whole (no no-result failures, which are
    `PARTIAL`'s territory and must not be relabelled past it). And the store's machine
    checks the serialized record: an authoritative record carries its evaluation, its
    `passed` is a boolean equal to the evaluation's own, and a non-authoritative record
    carries no `passed` at all -- so a hand-built record with the right version and
    invocation and no evidence is still refused at the primitive.
    """
    import os
    import shutil

    from behave.sim.authority import CONCRETE, INVOCATION, PARTIAL, RESULT_VERSION, Authoritative
    from behave.sim.core.outcome import ConcreteEvaluation
    from behave.sim.result_store import ResultStore

    class Fake:
        passed = True

        def as_dict(self):
            return {"passed": True, "results": []}

    # ── the constructor ──
    with pytest.raises(Refused, match="authority-evidence-not-an-evaluation"):
        Authoritative(CONCRETE, evaluation=Fake())

    class _Task:
        pass

    with pytest.raises(Refused, match="authority-evidence-empty"):
        Authoritative(CONCRETE, evaluation=ConcreteEvaluation(_Task(), results=()))

    # Whole: an evaluation carrying a no-result failure is PARTIAL's, and CONCRETE
    # around it would skip that rule. Staged with a real infrastructure failure.
    from behave.sim.core.run_record import RunResult

    task = api.admit(contract(), realization(), reference=_ref())
    stim = schedule([3])
    broken = ConcreteEvaluation(task, results=[
        RunResult.failed_to_run(
            stim,
            api.RunFailure.infrastructure(
                "test-replay",
                api.Refusal(
                    "simulator-unavailable",
                    "the simulator did not come up")))])
    assert broken.infrastructure
    with pytest.raises(Refused, match="authority-evidence-not-whole"):
        Authoritative(CONCRETE, evaluation=broken)

    # And the real thing constructs, which is what the helper every store test uses is.
    real = _concrete()
    assert real.passed in (True, False)

    # ── the machine, on the serialized record ──
    where = _repo() / "scratch" / f"union_{os.getpid()}"
    shutil.rmtree(where, ignore_errors=True)
    where.mkdir(parents=True)
    try:
        store = ResultStore(where)
        store.begin()
        stamped = {"kind": CONCRETE, "version": RESULT_VERSION,
                   "invocation": INVOCATION, "authoritative": True, "passed": True,
                   "why": "", "refusals": [], "telemetry": {}}
        with pytest.raises(ValueError, match="a score and no evidence"):
            store._commit(authority=stamped,
                          report={"result_kind": CONCRETE, "authority": stamped,
                                  "passed": True},
                          progress={"result_kind": CONCRETE, "passed": True},
                          terminal=True)
        lying = dict(stamped)
        lying["evaluation"] = {"passed": False, "results": [{"ran": True}]}
        with pytest.raises(ValueError, match="evaluation inside it says"):
            store._commit(authority=lying,
                          report={"result_kind": CONCRETE, "authority": lying,
                                  "passed": True},
                          progress={"result_kind": CONCRETE, "passed": True},
                          terminal=True)
        carrying = Authoritative(PARTIAL, why="x").as_dict()
        carrying["passed"] = True
        with pytest.raises(ValueError, match="carries no score"):
            store._commit(authority=carrying,
                          report={"result_kind": PARTIAL, "authority": carrying},
                          terminal=True)
    finally:
        shutil.rmtree(where, ignore_errors=True)
