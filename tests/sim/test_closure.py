"""Unit tests for goal classification over a witness pool.

Two properties matter here. A covered goal must carry the witness that reached
it, so the count can be rechecked by replay. And nothing may be called
unreachable on the strength of a pool alone.
"""
import pickle

import pytest

from behave.sim.behavior.goals import goal_set
from behave.sim.behavior.instrumented import classify_pool, load_instrumented, witness_hits
from behave.sim.core.audit_plan import AuditPlan

MODEL = '''
class M:
    def reset(self):
        self.n = 0

    def process(self, a):
        if a > 10:
            self.n += 1
            return {"y": 1}
        if a == 7:
            return {"y": 7}
        return {"y": 0}
'''


@pytest.fixture()
def model(tmp_path):
    p = tmp_path / "behavior.py"
    p.write_text(MODEL)
    return p


def test_refused_witness_keeps_native_failure_and_request_without_classifying():
    from behave.sim.core.audit import AuditCheck
    from behave.sim.core.closure import _finish_audited_query_witness
    from behave.sim.core.refusal import Refused

    detail = {'failure': {'origin': 'candidate', 'observed': ['assertion']},
              'stimulus': {'controls': [1, 0]}, 'initialization': {'q': 0}}
    refused = AuditCheck.refused('native-failed', 'source stopped', **detail)
    with pytest.raises(Refused) as raised:
        _finish_audited_query_witness(None, ('assertion',), (), None, refused)
    assert raised.value.refusal.as_dict() == {
        'kind': 'native-failed', 'why': 'source stopped',
        'detail': {**detail, 'goals': ['assertion']}}


def test_instrumentation_leaves_the_model_computing_what_it_computed(tmp_path):
    """The hook records the truth of a value and returns the value. Returning
    the truth instead makes `a or 7` evaluate to True, so what is measured is
    a run of another model."""
    source = ('class M:\n'
              '    def reset(self): pass\n'
              '    def process(self, a):\n'
              '        return {"y": a or 7, "z": a and 7}\n')
    path = tmp_path / "behavior.py"
    path.write_text(source)
    ns, _sites, _hits = load_instrumented(path)
    instrumented = next(v for v in ns.values()
                        if isinstance(v, type) and hasattr(v, "process"))
    for a in (0, 5):
        assert instrumented().process(a) == {"y": a or 7, "z": a and 7}


def test_every_goal_is_enumerated(model):
    goals = goal_set(model.read_text(), file="behavior.py")
    assert sum(goal.startswith("branch:") for goal in goals) == 4
    assert sum(goal.startswith("location:") for goal in goals) == 6


def test_a_witness_hits_the_outcomes_it_takes(model):
    got = witness_hits(model, [[20]])
    branches = {goal for goal in got if goal.startswith("branch:")}
    locations = {goal for goal in got if goal.startswith("location:")}
    assert sorted(goal[-1] for goal in branches) == ["T"]
    assert len(locations) == 3


def test_a_second_branch_is_reached_only_after_the_first_is_false(model):
    got = witness_hits(model, [[7]])
    # ``a > 10`` false, then ``a == 7`` true: one outcome of each site.
    branches = {goal for goal in got if goal.startswith("branch:")}
    locations = {goal for goal in got if goal.startswith("location:")}
    assert sorted(goal[-1] for goal in branches) == ["F", "T"]
    assert len(locations) == 3


def test_pool_covers_the_union_and_records_a_witness_per_goal(model):
    cls = classify_pool(model, [[[20]], [[7]], [[0]]])
    assert cls.summary()["n_goals"] == 10
    assert cls.summary()["n_covered"] == 10
    assert cls.summary()["n_undecided"] == 0
    # Each covered goal names the witness that first reached it.
    assert set(cls.witness_of.values()) == {0, 1, 2}


def test_uncovered_goals_are_undecided_not_unreachable(model):
    cls = classify_pool(model, [[[20]]])
    assert cls.summary()["n_covered"] == 4
    assert cls.summary()["n_unreachable"] == 0
    assert len(cls.undecided) == 6
    assert cls.residual == cls.undecided


def test_model_state_is_reset_per_witness(model):
    ns, sites, hits = load_instrumented(model)
    fresh = witness_hits(model, [[0]])
    witness_hits(model, [[20], [20]], ns, sites, hits)
    second = witness_hits(model, [[0]], ns, sites, hits)
    # The second witness starts from reset, so it reports only its own hits.
    assert second == fresh


def test_covered_percentage_is_over_the_enumerated_set(model):
    cls = classify_pool(model, [[[20]]])
    assert cls.summary()["pct_covered"] == 40.0


# ── the core procedure, on an artifact whose answers are written down ──
#
# The point of the instance protocol is that the procedure can be exercised
# without a frontend. If a claim about the procedure needs a Python model to
# reproduce, the boundary is not where it says it is.

from behave.sim.core import closure as closure_mod
from behave.sim.core import evidence as ev
from behave.sim.core.catalogue import catalogue_of
from behave.sim.core.instance import Feasible, Replay, Verdict

#: One-element traces, so a bound domain accepts them by length. Opaque inside: these
#: tests exercise the closure's decision logic and the domain declares no contract, so
#: what a step *is* is not this file's subject.
TR, BAD, POOL = ("tr",), ("bad",), ("pool",)


def bound1(inst, contract=None):
    """`inst` on a one-step domain, because `close_over` refuses an unbound instance.

    It used to accept a raw instance and a step count, wrap it so that `validate`
    returned an empty list for everything, and note in the certificate that the pool had
    gone unchecked. TODO3 T3-04 deletes that: a conclusion drawn over a pool nobody could
    check is a conclusion about a set nobody named.

    These tests are about the closure's decision logic, so the domain declares no
    contract -- there is nothing here to check a row against -- and the traces are
    one-element tuples so they have the domain's length.
    """
    from behave.sim.core.domain import TraceDomain
    from behave.sim.core.instance import BoundedInstance

    return BoundedInstance(inst, TraceDomain(bound=1, contract=contract or {},
                                             frontend="test/1"))


class Dummy:
    """An artifact whose answers are written down rather than computed."""

    def __init__(self, goals, verdicts=(), hits=None, ruled_out=None,
                 broken=()):
        # This test double is the explicit adapter for the historical ``id -> class``
        # shape.  Production closure receives the complete manifest and never performs
        # this lossy conversion at its evidence boundary.
        self._goals = catalogue_of(dict(goals), allow_legacy=True)
        self._verdicts = list(verdicts)
        self._hits = dict(hits or {})       # trace key -> the goals it reaches
        self._ruled_out = dict(ruled_out or {})
        self._broken = set(broken)
        self.replays = 0

    def goals(self):
        return self._goals

    def ruled_out(self):
        return dict(self._ruled_out)

    def replay(self, trace, state=None):
        self.replays += 1
        if trace in self._broken:
            return Replay((), broken="the simulator died")
        return Replay(set(self._hits.get(trace, ())))

    def query(self, goals, steps, **kw):
        return self._verdicts.pop(0)

    def feasible(self, steps, timeout_ms=None):
        return Feasible.YES, []

    def audit(self, traces):
        del traces
        from behave.sim.core.audit import AuditCheck
        return AuditCheck.agreed()

    def well_formed(self):
        return []

    def unaccounted_goals(self):
        return []

    def audit_plan(self):
        return AuditPlan()

    def budgets(self):
        return {}

    def contract(self):
        return {}


class IncrementalDummy(Dummy):
    def audit_request(self, rows, domain):
        from behave.sim.core.audit import AuditedWitness
        return AuditedWitness(domain=domain, inputs=rows, states=({}, {}),
            outputs=((),), hit_sets=(set().union(*self._hits.values()),))

    def replay(self, trace, state=None):
        from behave.sim.core.instance import StepOutput
        self.replays += 1
        return Replay(set().union(*self._hits.values()), outputs=[StepOutput()], state={}, steps=1)


@pytest.mark.parametrize('stop', ['budget', 'refusal'])
@pytest.mark.parametrize('different_providers', [False, True])
def test_incremental_queries_keep_audited_facts_before_interruption(stop, different_providers):
    from behave.sim.analysis_budget import AnalysisBudget, AnalysisStopped
    from behave.sim.core.query import QueryCall, SolverAnswer
    from behave.sim.core.refusal import Refusal, Refused

    class Incremental(IncrementalDummy):
        closed = False
        def query_steps(self, goals, steps, **kw):
            assert set(goals) == {'a', 'b', 'c'}
            try:
                yield Verdict({}, {'a'}, {}, queries=[QueryCall(('a',), SolverAnswer.UNSAT)])
                yield Verdict({'b': ({},)}, (), {}, queries=[
                    QueryCall(('b', 'c'), SolverAnswer.SAT, witness=[{}])])
                # The consumer must finish replay before resuming the solver.
                assert self.replays == 1
                if stop == 'budget':
                    raise AnalysisStopped(AnalysisBudget(1))
                raise Refused(Refusal('worker-unavailable', 'interrupted residual'))
            finally:
                self.closed = True

    from behave.sim.core.catalogue import Goal
    goals = {name: Goal(name, ev.COVERAGE, provider=(
        'hard-singleton' if name == 'c' else 'easy-batch') if different_providers else 'shared')
        for name in ('a', 'b', 'c')}
    inst = Incremental(goals, hits={TR: {'b'}})
    with pytest.raises(closure_mod.QueryRefused) as caught:
        closure_mod.close_over(bound1(inst))
    detail = caught.value.refusal.detail
    assert list(detail['completed_unreachable']) == ['a']
    assert list(detail['completed_reached']) == ['b']
    assert list(detail['pending']) == ['c']
    assert len(caught.value.queries) == 2
    assert caught.value.queries[1].replayed_hits == ('b',)
    assert inst.closed and inst.replays == 1


def test_incremental_query_stops_after_audited_collateral_hits_complete_its_goals():
    from behave.sim.core.query import QueryCall, SolverAnswer

    class Incremental(IncrementalDummy):
        closed = False

        def query_steps(self, goals, steps, **kw):
            assert set(goals) == {'a', 'b', 'c'}
            try:
                yield Verdict({}, {'a'}, {}, queries=[QueryCall(('a',), SolverAnswer.UNSAT)])
                yield Verdict({'b': ({},)}, (), {}, queries=[
                    QueryCall(('b',), SolverAnswer.SAT, witness=[{}])])
                pytest.fail('all goals were audited; resuming the stale query can only waste work')
            finally:
                self.closed = True

    inst = Incremental(dict.fromkeys(('a', 'b', 'c'), ev.COVERAGE), hits={TR: {'b', 'c'}})
    got = closure_mod.close_over(bound1(inst))
    assert set(got.reached) == {'b', 'c'}
    assert set(got.unreachable) == {'a'}
    assert not got.unresolved
    assert len(got.queries) == 2 and got.query_transcript_complete
    assert got.queries[1].replayed_hits == ('b', 'c')
    assert inst.closed and inst.replays == 1


@pytest.mark.parametrize('fault', ['omitted', 'duplicate', 'no-transcript', 'contradiction'])
def test_incremental_query_failures_cannot_publish_a_partition(fault):
    from behave.sim.core.query import QueryCall, SolverAnswer
    from behave.sim.core.refusal import Refused

    class Incremental(IncrementalDummy):
        closed = False
        def query_steps(self, goals, steps, **kw):
            try:
                yield Verdict({}, {'a'}, {}, queries=[QueryCall(('a',), SolverAnswer.UNSAT)])
                if fault == 'omitted':
                    return
                if fault == 'duplicate':
                    yield Verdict({}, {'a'}, {}, queries=[QueryCall(('a',), SolverAnswer.UNSAT)])
                elif fault == 'no-transcript':
                    yield Verdict({}, {'b'}, {})
                else:
                    yield Verdict({'b': ({},)}, (), {}, queries=[
                        QueryCall(('b',), SolverAnswer.SAT, witness=[{}])])
            finally:
                self.closed = True

    inst = Incremental(dict.fromkeys(('a', 'b'), ev.COVERAGE), hits={TR: {'a', 'b'}})
    with pytest.raises(closure_mod.WitnessInconsistent if fault == 'contradiction' else Refused):
        closure_mod.close_over(bound1(inst))
    assert inst.closed


def test_incremental_query_budget_counts_calls_not_end_of_stream():
    from behave.sim.core.query import QueryCall, SolverAnswer
    from behave.sim.core.query_cache import AnalysisRequestBudget
    from behave.sim.core.instance import BoundedInstance

    class Incremental(Dummy):
        def query_steps(self, goals, steps, **kw):
            yield Verdict({}, {'a'}, {}, queries=[QueryCall(('a',), SolverAnswer.UNSAT)])
            yield Verdict({}, {'b'}, {}, queries=[QueryCall(('b',), SolverAnswer.UNSAT)])

    inst = bound1(Incremental(dict.fromkeys(('a', 'b'), ev.COVERAGE)))
    inst = BoundedInstance(inst.instance, inst.domain, request_budget=AnalysisRequestBudget(2))
    got = closure_mod.close_over(inst)
    assert set(got.unreachable) == {'a', 'b'}
    assert inst.request_budget.used == 2


def test_a_pool_trace_of_another_domain_is_refused_before_it_is_replayed():
    """The theorem quantifies over the traces of this artifact at this bound under
    this contract, and nothing checked that the pool was made of them.

    `close_over` took a step count as an integer and the domain was assembled at the
    end of `certify`, so the record described what the closure had been about after it
    was over. A four-step trace counted towards a two-step closure, and a trace
    presenting an input the contract does not admit counted at all.

    Refused before the replay, not after: after is too late, the hits having been read
    off a run of something else. A refusal lands in `broken`, which the certificate
    already reads as "not every trace of the pool was replayed".
    """
    from behave.sim.core.domain import TraceDomain
    from behave.sim.core.instance import BoundedInstance

    inst = Dummy({"a": ev.COVERAGE},
                 verdicts=[Verdict({}, {"a"}, {})],
                 hits={(("x", 1),): {"a"}})
    domain = TraceDomain(bound=2, contract={"x": [0, 7]}, frontend="test/1",
                         policy="test-policy")
    bound = BoundedInstance(inst, domain)

    assert bound.validate([{"x": 1}, {"x": 2}]) == []
    assert "1 steps where the bound is 2" in bound.validate([{"x": 1}])[0]
    assert "outside 0..7" in bound.validate([{"x": 1}, {"x": 9}])[0]
    assert "does not declare" in bound.validate([{"x": 1}, {"y": 0}])[0]
    assert "omits x" in bound.validate([{"x": 1}, {}])[0]
    assert "integer interval" in bound.validate([{"x": 1}, {"x": "1"}])[0]
    assert "integer interval" in bound.validate([{"x": 1}, {"x": True}])[0]

    # A trace tagged with another domain's digest, which is how one crosses a
    # boundary. Nothing in a list of input rows says which frontend or policy
    # produced it, so a caller moving traces between domains says so and the
    # receiving side can refuse.
    elsewhere = BoundedInstance(inst, TraceDomain(bound=2, contract={"x": [0, 7]},
                                                  frontend="other/1"))
    assert "belongs to domain" in bound.validate(elsewhere.tag([{"x": 1},
                                                                {"x": 2}]))[0]
    assert bound.validate(bound.tag([{"x": 1}, {"x": 2}])) == []

    # And the closure records it rather than counting its hits.
    got = closure_mod.close_over(bound, pool=[[{"x": 1}]])
    assert len(got.broken) == 1 and "not a trace of this domain" in got.broken[0][1]
    assert got.of("a").status is not ev.GoalStatus.REACHED
    assert inst.replays == 0, "refused before the replay, not after"


def test_a_bound_instance_needs_no_step_count_and_reports_its_own_domain():
    """The bound is in one place, so nothing can disagree about it."""
    from behave.sim.core.domain import TraceDomain
    from behave.sim.core.instance import BoundedInstance

    inst = Dummy({"a": ev.COVERAGE}, verdicts=[Verdict({}, {"a"}, {})])
    domain = TraceDomain(bound=3, contract={"x": [0, 1]}, frontend="test/1")
    got = closure_mod.close_over(BoundedInstance(inst, domain))
    assert got.steps == 3, "taken from the domain rather than from an argument"
    assert got.domain == domain.digest()


def test_the_pool_is_traces_and_every_covered_goal_names_one():
    """A goal is covered because a trace reached it and the trace is on record.

    The procedure used to take the identifiers of goals a caller said were
    covered and write them down as reached with no witness. Nothing in that
    record can be rechecked: it does not say which trace reached the goal, how
    long it was, or whether it was ever run. What the theorem quantifies over
    is a set of audited traces, so that is what comes in.
    """
    inst = Dummy({"a": ev.COVERAGE, "b": ev.COVERAGE},
                 verdicts=[Verdict({}, {"b"}, {})],
                 hits={TR: {"a"}})
    got = closure_mod.close_over(bound1(inst), pool=[TR])
    assert got.of("a").status is ev.GoalStatus.REACHED
    assert got.of("a").origin is ev.Origin.POOL
    assert got.of("a").witness == TR, "the trace, not just the verdict"
    assert inst.replays >= 1, "the closure ran the pool itself"


def test_coordinator_audited_pool_is_not_replayed_or_queried_again():
    from behave.sim.core.audit import AuditedWitness

    inst = Dummy({'a': ev.COVERAGE, 'b': ev.COVERAGE},
                 verdicts=[Verdict({}, {'b'}, {})])
    exact = bound1(inst)
    trace = ({},)
    witness = AuditedWitness(domain=exact.domain.digest(), inputs=trace,
        states=({}, {}), outputs=((),), hit_sets=({'a'},))
    got = closure_mod.close_over(exact, pool=[trace], _audited_witnesses=(witness,))
    assert got.of('a').status is ev.GoalStatus.REACHED
    assert got.of('a').origin is ev.Origin.POOL
    assert got.of('b').status is ev.GoalStatus.UNREACHABLE
    assert inst.replays == 0


@pytest.mark.parametrize('fault', ['domain', 'request', 'goal', 'count', 'type'])
def test_coordinator_pool_handoff_rejects_mismatched_evidence(fault):
    from behave.sim.core.audit import AuditedWitness, WitnessInconsistent

    inst = Dummy({'a': ev.COVERAGE}, verdicts=[Verdict({}, {'a'}, {})])
    exact = bound1(inst)
    witness = AuditedWitness(
        domain='another-domain' if fault == 'domain' else exact.domain.digest(),
        inputs=({'x': 7},) if fault == 'request' else ({},),
        states=({}, {}), outputs=((),), hit_sets=({'unknown' if fault == 'goal' else 'a'},))
    pool = () if fault == 'count' else ('a',) if fault == 'type' else (witness,)
    with pytest.raises((TypeError, WitnessInconsistent)):
        closure_mod.close_over(exact, pool=[({},)], _audited_witnesses=pool)
    assert inst.replays == 0


def test_a_pool_trace_that_could_not_be_run_is_not_evidence():
    """Broken is not halted. A trace this side could not carry out supports
    nothing, and is reported apart from any goal's status."""
    inst = Dummy({"a": ev.COVERAGE},
                 verdicts=[Verdict({}, {"a"}, {})],
                 hits={BAD: {"a"}}, broken={BAD})
    got = closure_mod.close_over(bound1(inst), pool=[BAD])
    assert got.of("a").status is not ev.GoalStatus.REACHED
    assert len(got.broken) == 1


def test_a_witness_settles_a_goal_whose_own_query_timed_out():
    """The merge is a priority, not the order things happen to be recorded in.

    Reproduced from the review: `a`'s query gives up, `b`'s witness reaches
    both, and `a` used to be reported undecided with the trace that reaches it
    sitting in the same round. `setdefault` decided it, so whichever claim was
    written first won.
    """
    inst = Dummy({"a": ev.COVERAGE, "b": ev.COVERAGE},
                 verdicts=[Verdict({"b": TR}, set(), {"a": "timeout"})],
                 hits={TR: {"a", "b"}})
    got = closure_mod.close_over(bound1(inst), batch=8)
    assert got.of("a").status is ev.GoalStatus.REACHED
    assert got.of("a").witness == TR
    assert got.of("b").status is ev.GoalStatus.REACHED


def test_a_run_that_reaches_an_unreachable_goal_is_a_fault_in_the_prover():
    """Whichever claim called it unreachable, and whenever. A query in the same
    round has no more standing than one in an earlier round or the construct."""
    inst = Dummy({"a": ev.COVERAGE, "b": ev.COVERAGE},
                 verdicts=[Verdict({"b": TR}, {"a"}, {})],
                 hits={TR: {"a", "b"}})
    with pytest.raises(
            closure_mod.WitnessInconsistent,
            match="exact query called bounded unreachable") as stopped:
        closure_mod.close_over(bound1(inst), batch=8)
    assert stopped.value.kind == "closure-unreachable-refuted"
    assert stopped.value.detail["goals"] == ["a"]


def test_a_construction_claim_a_run_contradicts_is_a_fault_too():
    """`ruled_out` is the frontend's word. A trace of the pool reaching a goal
    the frontend said its own construct rules out contradicts that word."""
    inst = Dummy({"a": ev.COVERAGE},
                 ruled_out={"a": "the iterable has 3 elements"},
                 hits={TR: {"a"}})
    with pytest.raises(
            closure_mod.WitnessInconsistent,
            match="frontend ruled out during construction") as stopped:
        closure_mod.close_over(bound1(inst), pool=[TR])
    assert stopped.value.kind == "closure-construction-claim-refuted"
    assert stopped.value.detail["goals"] == ["a"]


def test_a_provider_witness_disagreement_is_not_an_unavailable_audit(monkeypatch):
    from behave.sim.core.audit import AuditCheckStatus, WitnessInconsistent
    from behave.sim.core.query import QueryCall, SolverAnswer

    assert closure_mod.WitnessInconsistent is WitnessInconsistent
    inst = Dummy({"a": ev.COVERAGE},
                 verdicts=[Verdict({"a": TR}, set(), {}, queries=(
                     QueryCall(("a",), SolverAnswer.SAT, witness=list(TR)),))],
                 hits={TR: {"a"}})

    def disagree(*_args):
        raise WitnessInconsistent("source-disagrees", "two completed readings differ")

    monkeypatch.setattr(inst, "audit_request", disagree, raising=False)
    monkeypatch.setattr(inst, "replay", lambda *_args: Replay({"a"}))
    bound = bound1(inst)
    assert bound.audit_request(TR).status is AuditCheckStatus.INCONSISTENT
    with pytest.raises(WitnessInconsistent):
        closure_mod.close_over(bound, batch=8)


def test_a_construction_claim_is_asked_anyway_and_the_status_rests_on_the_query():
    """`ruled_out` used to write `UNREACHABLE` with `Origin.CONSTRUCTION` straight in,
    and the query loop then **skipped** those goals -- so a goal was certified
    unreachable on the frontend's word and nothing ever tested it.

    TODO3 T3-04: certified unreachability comes only from an exact horizon query
    returning UNSAT, never from frontend `ruled_out`, construction-time simplification,
    or a local probe. The claim is recorded and the goal is asked.
    """
    inst = Dummy({"a": ev.COVERAGE},
                 ruled_out={"a": "the iterable has 3 elements"},
                 verdicts=[Verdict({}, {"a"}, {})])
    got = closure_mod.close_over(bound1(inst))
    assert got.of("a").status is ev.GoalStatus.UNREACHABLE
    assert got.of("a").origin is ev.Origin.QUERY, "the query, not the construct"
    assert got.construction_claims == {"a": "the iterable has 3 elements"}
    assert got.summary()["n_ruled_out_by_construction"] == 1
    assert got.summary()["n_construction_claims_proved"] == 1
    assert "n_dead" not in got.summary(), (
        "unreachable at a bound under a contract is not deadness")


def test_a_construction_claim_a_query_cannot_settle_does_not_become_unreachable():
    """The case the old shape hid entirely. A frontend claim the solver cannot confirm
    left the goal `UNREACHABLE` before, because the claim was the status; it is now
    `UNRESOLVED`, which is what nobody having established it means."""
    inst = Dummy({"a": ev.COVERAGE},
                 ruled_out={"a": "the lowering emits no site for it"},
                 verdicts=[Verdict({}, set(), {"a": "timeout"})])
    got = closure_mod.close_over(bound1(inst))
    assert got.of("a").status is ev.GoalStatus.UNRESOLVED
    assert got.summary()["n_ruled_out_by_construction"] == 1
    assert got.summary()["n_construction_claims_proved"] == 0


def test_an_empty_batch_is_refused():
    """It asks the empty question forever: three million queries in three
    seconds, and the residual never shrinks."""
    inst = Dummy({"a": ev.COVERAGE})
    with pytest.raises(ValueError, match="positive integer"):
        closure_mod.close_over(bound1(inst), batch=0)


@pytest.mark.parametrize("batch", [True, 1.5, "1"])
def test_a_batch_is_an_exact_positive_integer(batch):
    inst = Dummy({"a": ev.COVERAGE})
    with pytest.raises(ValueError, match="positive integer"):
        closure_mod.close_over(bound1(inst), batch=batch)


def test_every_goal_gets_exactly_one_status():
    inst = Dummy({"a": ev.COVERAGE, "b": ev.SAFETY, "c": ev.COVERAGE},
                 verdicts=[Verdict({"b": TR}, {"c"}, {})],
                 hits={TR: {"b"}, POOL: {"a"}})
    got = closure_mod.close_over(bound1(inst), pool=[POOL])
    assert got.summary()["accounted_for"]
    assert set(got.evidence) == {"a", "b", "c"}


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize("batch,expected", [
    (None, [("z",), ("a", "b")]),
    (2, [("a", "b"), ("z",)]),
    (8, [("a", "b", "z")]),
])
def test_provider_rounds_respect_streaming_owner_and_explicit_batch(batch, expected, streaming):
    from behave.sim.core.catalogue import Goal
    from behave.sim.core.query import QueryCall, SolverAnswer

    goals = {name: Goal(name, ev.COVERAGE, provider=provider)
             for name, provider in (("b", "large"), ("z", "small"), ("a", "large"))}
    inst = Dummy(goals)
    calls = []

    def query(asking, steps, **_limits):
        calls.append(tuple(asking))
        return Verdict({}, set(asking), {}, queries=(
            QueryCall(asking, SolverAnswer.UNSAT),))

    inst.query = query
    if streaming:
        def query_steps(asking, steps, **limits):
            yield query(asking, steps, **limits)
        inst.query_steps = query_steps
        if batch is None:
            expected = [("a", "b", "z")]
    got = closure_mod.close_over(bound1(inst), batch=batch)
    assert calls == expected
    assert got.unreachable == set(goals)
    assert got.summary()["accounted_for"]


def test_provider_rounds_credit_cross_provider_witness_hits_without_more_queries():
    from behave.sim.core.audit import AuditedWitness
    from behave.sim.core.catalogue import Goal
    from behave.sim.core.instance import StepOutput
    from behave.sim.core.query import QueryCall, SolverAnswer

    goals = {name: Goal(name, ev.COVERAGE, provider=provider)
             for name, provider in (("a", "large"), ("b", "large"), ("z", "small"))}
    inst = Dummy(goals)
    trace = ({},)
    calls, audits = [], []

    def audit_request(rows, domain):
        audits.append(rows)
        return AuditedWitness(domain=domain, inputs=rows,
            states=({}, {}), outputs=((),), hit_sets=(set(goals),))

    def query(asking, steps, **_limits):
        calls.append(tuple(asking))
        return Verdict({"z": trace}, set(), {}, queries=(
            QueryCall(asking, SolverAnswer.SAT, witness=list(trace)),))

    inst.query = query
    inst.audit_request = audit_request
    inst.replay = lambda _trace: Replay(set(goals), outputs=[StepOutput()], state={}, steps=1)
    got = closure_mod.close_over(bound1(inst))
    assert calls == [("z",)]
    assert len(audits) == 1
    assert got.reached == set(goals)
    assert got.summary()["accounted_for"]

def test_the_class_comes_from_the_catalogue_not_from_the_name():
    """`startswith("safety:")` was the rule, and it would not have survived an
    RTL goal, which is not named after a Python source position. Worse, it
    failed silently in the direction that hides failures: an identifier under
    another convention became coverage rather than raising."""
    from behave.sim.core.catalogue import COVERAGE, SAFETY, Goal

    inst = Dummy({"anything at all": Goal("anything at all", SAFETY),
                  "safety:looks-like-one": Goal("safety:looks-like-one",
                                                COVERAGE)},
                 verdicts=[Verdict({}, {"anything at all",
                                        "safety:looks-like-one"}, {})])
    got = closure_mod.close_over(bound1(inst))
    assert got.with_status(ev.GoalStatus.UNREACHABLE, SAFETY) == \
        {"anything at all"}
    assert got.with_status(ev.GoalStatus.UNREACHABLE, COVERAGE) == \
        {"safety:looks-like-one"}


def test_an_explicit_legacy_frontend_adapter_still_works():
    """A legacy class map is converted before the production evidence boundary."""
    inst = Dummy({"a": ev.COVERAGE},
                 verdicts=[Verdict({}, {"a"}, {})])
    got = closure_mod.close_over(bound1(inst))
    assert got.of_goal("a").cls == ev.COVERAGE
    assert got.classes == {"a": ev.COVERAGE}


def test_the_report_prints_where_a_goal_lives_without_parsing_its_name():
    """The core used to split the identifier on colons and read the second
    field as a line number. That is one frontend's convention read by the
    module that must not know which frontend it is talking to."""
    from behave.sim.core.catalogue import COVERAGE, Goal

    inst = Dummy({"whatever": Goal("whatever", COVERAGE,
                                   provenance={"line": 2})},
                 verdicts=[Verdict({}, {"whatever"}, {})])
    got = closure_mod.close_over(bound1(inst))
    text = closure_mod.report(got, source="first line\nsecond line\n")
    assert "second line" in text


# ── P1: the whole open set goes to the backend ──

def test_the_open_set_goes_to_the_backend_whole():
    """P1. `batch` defaulted to eight, and `residual.decide_goals` splits its goals
    across `cpu_count - 2` workers -- so eight goals and eight cores gave **one goal per
    worker**: the set query degenerated to eight single-goal queries and every worker
    rebuilt the whole unrolling to ask one of them. Both halves of the design were
    cancelled by two defaults that were each chosen alone.

    Measured on a fixed sample of the population, total closure time fell **44.7s to
    27.5s, 39%**, and the slowest model fell from 34.9s to 18.0s. That one is nineteen
    goals: three rounds of eight workers became one round of nineteen, and the rounds
    were the cost.
    """
    asked = []

    class Watching(Dummy):
        def query(self, goals, steps, **kw):
            asked.append(list(goals))
            return super().query(goals, steps, **kw)

    goals = {f"g{i}": ev.COVERAGE for i in range(20)}
    inst = Watching(goals, verdicts=[Verdict({}, set(goals), {})])
    closure_mod.close_over(bound1(inst))
    assert len(asked) == 1, f"the open set was cut into {len(asked)} rounds"
    assert len(asked[0]) == 20


def test_a_caller_can_still_bound_a_round():
    """The parameter stays, because a caller that wants a bounded round should be able
    to say so; what changed is which way round the default is."""
    asked = []

    class Watching(Dummy):
        def query(self, goals, steps, **kw):
            asked.append(list(goals))
            return Verdict({}, set(), {goal: "timeout" for goal in goals})

    goals = {f"g{i}": ev.COVERAGE for i in range(20)}
    inst = Watching(goals, verdicts=[Verdict({}, set(), {}) for _ in range(3)])
    closure_mod.close_over(bound1(inst), batch=8)
    assert [len(a) for a in asked] == [8, 8, 4]


def test_a_query_cannot_make_an_unasked_goal_unreachable():
    from behave.sim.core.refusal import Refused

    inst = Dummy(
        {"a": ev.COVERAGE, "b": ev.COVERAGE},
        verdicts=[Verdict({}, {"b"}, {})])

    with pytest.raises(Refused, match="closure-query-goal-set-mismatch"):
        closure_mod.close_over(bound1(inst), batch=1)


def test_a_query_must_answer_every_goal_it_was_asked():
    from behave.sim.core.refusal import Refused

    inst = Dummy({"a": ev.COVERAGE}, verdicts=[Verdict({}, set(), {})])

    with pytest.raises(Refused, match="closure-query-goal-set-mismatch"):
        closure_mod.close_over(bound1(inst))


# ── the transition bypasses TODO3 T3-04 asks to delete, and what replaces them ──

def test_a_raw_instance_is_refused_rather_than_wrapped_in_a_validator_that_passes():
    """`_Unbound` wrapped a raw instance so `validate` returned an empty list for
    everything, and the certificate carried a note saying the pool had gone unchecked.

    A note is not a refusal. The theorem's premise is that the pool is drawn from the
    traces of this artifact at this bound under this contract, and a procedure that
    proceeds without being able to check that has produced a conclusion about a set
    nobody named. `_Unbound`'s own docstring called it transitional.
    """
    from behave.sim.core.refusal import Refused

    inst = Dummy({"a": ev.COVERAGE}, verdicts=[Verdict({}, {"a"}, {})])
    with pytest.raises(Refused, match="closure-unbound-instance"):
        closure_mod.close_over(inst, steps=1)
    # And the bound form still works, so this is a refusal and not a removal.
    assert closure_mod.close_over(bound1(inst)).steps == 1


def test_an_object_cannot_spoof_the_bound_instance_boundary_with_two_attributes():
    """A domain and a no-op ``validate`` method do not make a bound instance.

    Otherwise the exact-provider object itself can recreate the deleted ``_Unbound``
    bypass: claim any domain, accept every pool member, and enter the query path without
    the closed adapter that owns the validation semantics.
    """
    from behave.sim.core.domain import TraceDomain
    from behave.sim.core.refusal import Refused

    class ForgedBound(Dummy):
        domain = TraceDomain(bound=1, frontend="forged/1")

        @staticmethod
        def validate(_trace):
            return []

    forged = ForgedBound({})
    with pytest.raises(Refused, match="closure-unbound-instance"):
        closure_mod.close_over(forged)


def test_a_replayed_pool_hit_outside_the_catalogue_stops_the_whole_closure():
    inst = Dummy({"a": ev.COVERAGE}, hits={POOL: {"a", "ghost"}})

    with pytest.raises(
            closure_mod.WitnessInconsistent, match="outside the goal catalogue"):
        closure_mod.close_over(bound1(inst), pool=(POOL,))


def test_a_step_that_is_not_a_row_contradicts_a_declared_contract():
    """`validate` skipped a non-mapping row with the comment "a frontend whose steps are
    not name-to-value rows". That branch is only reachable when the domain **declares a
    contract**, which is itself the claim that a step is such a row -- so the step
    contradicts the domain rather than being a shape it has no opinion about. Skipped, it
    was replayed and its hits were counted.
    """
    from behave.sim.core.domain import TraceDomain
    from behave.sim.core.instance import BoundedInstance

    inst = Dummy({"a": ev.COVERAGE})
    contracted = BoundedInstance(inst, TraceDomain(bound=1, contract={"x": [0, 7]}))
    assert contracted.validate([{"x": 1}]) == []
    why = contracted.validate(["not a row"])
    assert why and "not a name-to-value row" in why[0]

    # A frontend whose steps really are not rows declares no contract, and then this
    # domain has nothing to say about their shape and says nothing.
    open_domain = BoundedInstance(inst, TraceDomain(bound=1, contract={}))
    assert open_domain.validate(["not a row"]) == []


def test_feasibility_has_no_truth_value_because_it_has_three_answers():
    """`NO` and `UNRESOLVED` used to be both falsy, defended on the grounds that a caller
    asking whether feasibility was *established* gets the right answer from
    `if not feasible`.

    That is one of the two readings of that line. The other -- "the domain is empty" --
    is a finding about the artifact, where `UNRESOLVED` is a finding about the budget,
    and keeping those apart is the entire reason the enum has three members. A type whose
    obvious reading is the wrong one is not made safe by a docstring.

    It raises rather than being untruthy: an enum member is truthy by default, so simply
    deleting `__bool__` would turn every existing `if not feasible` into a branch that is
    always skipped, which is a wrong answer nobody sees.
    """
    from behave.sim.core.instance import Feasible

    assert Feasible.YES.established and not Feasible.YES.refuted
    assert Feasible.NO.refuted and not Feasible.NO.established
    assert Feasible.UNRESOLVED.unresolved and not Feasible.UNRESOLVED.established
    assert not Feasible.UNRESOLVED.refuted, "not a weaker no"

    for answer in Feasible:
        with pytest.raises(TypeError, match="no truth value"):
            bool(answer)


def test_a_bound_instance_names_what_it_passes_through():
    """`__getattr__` forwarded everything, defended on the grounds that listing would
    make the wrapper a second place deciding which questions a frontend may be asked.

    The cost of not listing is larger. A wrapper whose job is to be the gate forwarded,
    unexamined, every question nobody thought to name -- a misspelling included, which
    reached the frontend and raised there as if the frontend were at fault. What the
    bound changes is `goals`, `replay`, `query` and `feasible`; everything else is a
    question about the artifact that the bound does not change, and adding one is now a
    decision rather than an accident.
    """
    from behave.sim.core.instance import BoundedInstance

    inst = Dummy({"a": ev.COVERAGE}, ruled_out={"a": "because"})
    b = bound1(inst)
    assert b.ruled_out() == {"a": "because"}, "a listed question still passes"
    assert b.well_formed() == []

    def read_misspelled_passthrough():
        return b.rulded_out

    with pytest.raises(AttributeError, match="does not pass 'rulded_out' through"):
        read_misspelled_passthrough()
    assert "goals" not in BoundedInstance.PASSTHROUGH, (
        "the four the bound changes are methods, not passthroughs")


# ── what makes a goal that goal ──

def test_two_goals_with_one_identifier_and_different_metadata_are_not_equal():
    """Equality was over `(id, cls)` alone, so a stale record and a current one were
    interchangeable: same identifier, different family or provider or policy, and the
    catalogue could hold either without anything noticing.

    TODO3 T3-05 states the rule the other way -- reusing one ID with a different class,
    family, event, provider, policy or provenance is an error -- and an equality that
    cannot see the difference is what made it impossible to raise.
    """
    from behave.sim.core.catalogue import COVERAGE, Goal

    a = Goal("g", COVERAGE, family="branch", provider="lowering", policy="v1")
    b = Goal("g", COVERAGE, family="branch", provider="lowering", policy="v2")
    assert a != b and len({a, b}) == 2
    assert a.conflict_with(b) == {"policy": ("v1", "v2")}
    assert a == Goal("g", COVERAGE, family="branch", provider="lowering", policy="v1")

    # Provenance is a mapping, and two records built in a different order are one record.
    p = Goal("g", COVERAGE, provenance={"file": "m.py", "line": 3})
    q = Goal("g", COVERAGE, provenance={"line": 3, "file": "m.py"})
    assert p == q


def test_two_conflicting_records_for_one_goal_fail_closed():
    """Taking either would make the catalogue depend on registration order."""
    from behave.sim.core.catalogue import COVERAGE, Goal, catalogue_of
    from behave.sim.core.refusal import Refused

    good = [Goal("a", COVERAGE, family="branch"), Goal("b", COVERAGE)]
    assert set(catalogue_of(good)) == {"a", "b"}
    assert catalogue_of(good) == catalogue_of(list(reversed(good))), \
        "registration order does not change the catalogue"

    with pytest.raises(Refused, match="catalogue-conflicting-goal"):
        catalogue_of([Goal("a", COVERAGE, family="branch"),
                      Goal("a", COVERAGE, family="toggle")])


def test_a_legacy_mapping_is_coerced_only_while_a_caller_still_allows_it():
    """`{id: class}` loses the family, the provider, the event, the provenance and the
    policy -- and the policy is what a certificate's goal count is relative to, so a
    catalogue coerced from a mapping produces a certificate that cannot say what set it
    counted.

    Only an explicit historical reader may still request this conversion.  The default
    and every production evidence boundary refuse it.
    """
    from behave.sim.core.catalogue import COVERAGE, catalogue_of
    from behave.sim.core.refusal import Refused

    assert catalogue_of({"a": COVERAGE}, allow_legacy=True)["a"].policy is None
    with pytest.raises(Refused, match="catalogue-legacy-mapping"):
        catalogue_of({"a": COVERAGE})


def test_a_closure_carries_the_domain_it_was_gathered_under():
    """A closure used to carry only `steps`, so one gathered under one domain could be
    published against an instance bound to another with the same bound and a different
    contract, frontend or policy. Equality of one field out of eight is not equality.
    """
    from behave.sim.core.domain import TraceDomain
    from behave.sim.core.instance import BoundedInstance

    inst = Dummy({"a": ev.COVERAGE}, verdicts=[Verdict({}, {"a"}, {})])
    here = BoundedInstance(inst, TraceDomain(bound=1, contract={}, frontend="test/1"))
    got = closure_mod.close_over(here)
    assert got.domain == here.domain.digest()

    elsewhere = BoundedInstance(
        Dummy({"a": ev.COVERAGE}),
        TraceDomain(bound=1, contract={}, frontend="other/1"))
    assert got.domain != elsewhere.domain.digest()


def test_the_audits_are_admitted_before_the_closure_spends_anything():
    """`certify` checked them, and that was too late: the closure it is handed has
    already replayed the pool and already asked the queries, so a run refused there was
    refused after it was paid for."""
    from behave.sim.core.refusal import Refused

    class Counting(Dummy):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.asked = 0

        def query(self, goals, steps, **kw):
            self.asked += 1
            return super().query(goals, steps, **kw)

        def audit_plan(self):
            return ("goal_set_audit", "no_such_audit")

    inst = Counting({"a": ev.COVERAGE}, verdicts=[Verdict({}, {"a"}, {})])
    with pytest.raises(Refused, match="closure-audit-plan-invalid"):
        closure_mod.close_over(bound1(inst))
    assert inst.asked == 0 and inst.replays == 0


@pytest.mark.parametrize(
    ("reachable", "unreachable", "undecided"),
    [
        ({object(): []}, set(), {}),
        ({}, {object()}, {}),
        ({}, set(), {object(): "timeout"}),
    ],
)
def test_query_verdict_does_not_coerce_goal_identifiers(
        reachable, unreachable, undecided):
    with pytest.raises(TypeError, match="exact goal strings"):
        Verdict(reachable, unreachable, undecided)


def test_query_verdict_does_not_coerce_unresolved_reasons():
    class LooksLikeTimeout:
        def __str__(self):
            return "timeout"

    with pytest.raises(TypeError, match="exact string reason"):
        Verdict({}, set(), {"a": LooksLikeTimeout()})


def test_query_verdict_is_a_closed_deeply_frozen_record():
    witness = [{"a": [1]}]
    verdict = Verdict(
        {"reached": witness}, {"unreachable"}, {"open": "timeout"})

    witness[0]["a"][0] = 99
    verdict.reachable["reached"][0]["a"] = (88,)
    verdict.unreachable.clear()
    verdict.undecided["open"] = "forged"

    assert verdict.reachable == {"reached": ({"a": (1,)},)}
    assert verdict.unreachable == {"unreachable"}
    assert verdict.undecided == {"open": "timeout"}
    assert pickle.loads(pickle.dumps(verdict)).as_dict() == verdict.as_dict()
    with pytest.raises(AttributeError, match="cannot be reassigned"):
        verdict.queries = ()


def test_closure_records_are_closed_deeply_frozen_values():
    witness = ({"a": [1]},)
    item = ev.GoalEvidence(
        ev.GoalStatus.REACHED, ev.Origin.POOL, witness=witness)
    evidence = {"a": item}
    claims = {"a": "the frontend claimed this"}
    got = closure_mod.Closure(
        evidence,
        {"a": ev.COVERAGE},
        steps=1,
        broken=[(0, "the replay failed")],
        construction_claims=claims,
        allow_legacy=True,
    )

    witness[0]["a"][0] = 99
    evidence.clear()
    claims.clear()
    got.of("a").witness[0]["a"] = (88,)

    assert got.of("a").witness == ({"a": (1,)},)
    assert set(got.evidence) == {"a"}
    assert got.construction_claims == {"a": "the frontend claimed this"}
    assert pickle.loads(pickle.dumps(got)).summary() == got.summary()
    with pytest.raises(AttributeError, match="cannot be reassigned"):
        got.broken = ()
    with pytest.raises(TypeError):
        got.evidence["forged"] = item


def test_closure_records_refuse_open_or_coerced_phase_values():
    class LooksLikeStatus:
        value = "reached"

    with pytest.raises(TypeError, match="exact GoalStatus"):
        ev.GoalEvidence(LooksLikeStatus(), ev.Origin.POOL)
    with pytest.raises(TypeError, match="exact GoalEvidence"):
        closure_mod.Closure(
            {"a": object()}, {"a": ev.COVERAGE}, steps=1,
            allow_legacy=True)
    with pytest.raises(TypeError, match="exact Boolean"):
        closure_mod.Closure(
            {}, {}, steps=1, query_transcript_complete=1,
            allow_legacy=True)
