"""Array NBAs share the exact queue, but never replace recursive RTL storage."""

import pytest

from behave.sim.symbolic import encoder, interp, ir, ranges, typecheck as types
from behave.sim.engines import probing
from behave.sim.engines.rtl.source_query import query_source_interval
from behave.sim.rtl.source_deferred_array import ArrayDeferredWrite
from behave.sim.rtl.source_integral import IntegralType
from behave.sim.rtl.source_ir import _sequence
from behave.sim.rtl.source_values import ArrayType

from .test_rtl_source_deferred_ir import _bits
from .test_rtl_source_locations import _prepare_clocked_replay
from .test_rtl_source_query import _goal
from .test_rtl_source_scheduler import _lower, _names, _settle, _state, _step


def _packed(term, kind):
    import z3

    if isinstance(kind, ArrayType):
        from behave.sim.symbolic.functional_array import FunctionalArray
        if isinstance(term, FunctionalArray):
            term = [term.select(z3.BitVecVal(i, kind.cells.bit_length() + 1)) for i in range(kind.cells)]
        return z3.Concat(*[_packed(cell, kind.element) for cell in reversed(term)])
    return _bits(term, kind.width)


@pytest.mark.parametrize('width', [1, 4, 32])
@pytest.mark.parametrize('signed', [False, True])
@pytest.mark.parametrize('mask_kind', ['implicit', 'explicit', 'partial', 'symbolic'])
def test_array_word_overwrite_has_no_selected_old_data_dependency(width, signed, mask_kind):
    kind = IntegralType(width, signed)
    array = ArrayType(1, 0, kind)
    write = ArrayDeferredWrite('q', 'data', 'valid', 'first', 'varied', 'mask', array.width,
                              array=array, rises='rises', falls='falls')
    mask = {'implicit': None, 'explicit': ir.Lit((1 << width) - 1),
            'partial': ir.Lit(0), 'symbolic': ir.Var('input_mask')}[mask_kind]
    commands = write.enqueue_selected(ir.Var('value'), kind, (ir.Var('address'),), mask,
                                      lambda: 'scratch')
    update = next(command.expr for command in commands
                  if isinstance(command, ir.Assign) and command.var == write.data)
    assert update.op == 'store'  # Other cells still retain their previous values.
    payload = update.args[2]
    assert (write.data not in ir._reads(payload)) == (mask_kind in {'implicit', 'explicit'})
    # First/changed/rise/fall summaries still need earlier queue values even
    # for a full payload replacement: an overwritten value can trigger events.
    assert write.data in set().union(*(ir._reads(command.expr) for command in commands
        if isinstance(command, ir.Assign) and command.var in (write.rises, write.falls)))


@pytest.mark.solver
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("signed", [False, True])
def test_array_queue_equals_ordered_masked_commits_for_all_values(nested, signed):
    import z3

    kind = ArrayType(3, 2, IntegralType(4, signed))
    if nested:
        kind = ArrayType(-1, 0, kind)
    width = kind.width
    write = ArrayDeferredWrite("q", "last", "valid", "first", "varied", "written", width,
                              array=kind, rises="rises", falls="falls")
    inputs = ["precommit", "v0", "v1", "m0", "m1", "g0", "g1"]
    types_ = dict.fromkeys(["changed", *write.registers, *inputs], types.INTEGER)
    types_.update(q=kind.ir_type, precommit=kind.ir_type, valid=types.BOOLEAN,
                  g0=types.BOOLEAN, g1=types.BOOLEAN)
    types_.update((name, kind.ir_type) for name in (*write.registers, "changed", "v0", "v1", "m0", "m1")
                  if name != write.valid)
    types_.update(commit_rise=kind.ir_type, commit_fall=kind.ir_type)

    def temp(*, kind=types.INTEGER):
        name = f"scratch{len(types_)}"
        types_[name] = kind
        return name

    commands = list(write.initialize(temp))
    def enqueue(kind, path, value, mask):
        if isinstance(kind, ArrayType):
            return [command for i in range(kind.cells) for command in enqueue(kind.element,
                (*path, ir.Lit(i)), ir.Sel(value, ir.Lit(i)), ir.Sel(mask, ir.Lit(i)))]
        return write.enqueue_selected(value, kind, path, mask, temp)
    for i in range(2):
        commands.append(ir.If(ir.Var(f"g{i}"),
            _sequence(enqueue(kind, (), ir.Var(f"v{i}"), ir.Var(f"m{i}"))), ir.Skip()))
    commands.append(ir.Assign("q", ir.Var("precommit")))
    from behave.sim.rtl.source_values import zero_value
    def events(kind, path=()):
        if isinstance(kind, ArrayType):
            return [command for i in range(kind.cells) for command in events(kind.element, (*path, ir.Lit(i)))]
        from behave.sim.rtl.source_deferred_array import _store
        masks = write.event_masks(kind, path)
        return [_store("commit_rise", path, masks["rise"]), _store("commit_fall", path, masks["fall"])]
    commands.extend((ir.Assign("commit_rise", zero_value(kind)), ir.Assign("commit_fall", zero_value(kind))))
    commands.extend(events(kind))
    commands.extend(write.commit("changed", temp))
    outputs = ["changed", "commit_rise", "commit_fall"]
    proc = ir.Procedure(["q"], inputs, [n for n in types_ if n not in {"q", *outputs, *inputs}],
                        outputs, _sequence(commands), types=types_)
    domains = {name: ranges.Range(0, 1 if name.startswith("g") else (1 << width) - 1)
               for name in inputs}
    domains.update(q=kind.domain, precommit=kind.domain)
    domains.update(v0=kind.domain, v1=kind.domain, m0=write.mask_kind.domain, m1=write.mask_kind.domain)
    enc = encoder.encode_step(proc, env=ranges.fixpoint(proc, domains))
    previous, changed = _packed(enc.pre["precommit"], kind), z3.BitVecVal(0, width)
    rises = falls = z3.BitVecVal(0, width)
    for i in range(2):
        value, mask = _packed(enc.pre[f"v{i}"], kind), _packed(enc.pre[f"m{i}"], kind)
        enabled = enc.pre[f"g{i}"] != 0
        changed = z3.If(enabled, changed | ((previous ^ value) & mask), changed)
        rises = z3.If(enabled, rises | ((previous ^ value) & mask & value), rises)
        falls = z3.If(enabled, falls | ((previous ^ value) & mask & previous), falls)
        previous = z3.If(enabled, (previous & ~mask) | (value & mask), previous)
    solver = z3.Solver()
    solver.add(enc.constraint, z3.Or(_packed(enc.post["q"], kind) != previous,
                                     _packed(enc.post["changed"], kind) != changed,
                                     _packed(enc.post["commit_rise"], kind) != rises,
                                     _packed(enc.post["commit_fall"], kind) != falls))
    assert solver.check() == z3.unsat


@pytest.mark.eda("slang")
def test_large_ram_nba_lowering_has_no_ram_sized_carrier_or_full_ram_loop(tmp_path):
    source = """module M(input clk,input [15:0] addr,input [31:0] a,output logic [31:0] q);
      logic [31:0] mem[65536];
      always @(posedge clk) mem[addr]<=a;
      always_comb q=mem[50000];
    endmodule"""
    machine, _ = _lower(tmp_path, source)
    write, _, _ = machine.queue_edges[0]
    assert isinstance(write, ArrayDeferredWrite) and write.width == 2097152
    for name in write.registers:
        assert machine.procedure.types[name] == (types.BOOLEAN if name == write.valid else types.Array(65536))
    assert not any(isinstance(command, ir.For) for command in ir.walk(machine.procedure.body))
    assert max(abs(node.value).bit_length() for node in ir.expression_nodes(machine.procedure.body)
               if isinstance(node, ir.Lit)) <= 32


@pytest.mark.eda("slang", "verilator", "make")
@pytest.mark.solver
@pytest.mark.parametrize("partial", [False, True])
def test_dynamic_nested_array_nba_values_hits_and_search_match_native(tmp_path, partial):
    target = "x[4+a[0]][3-a[1]]"
    body = (f"{target}[3:2]<=(-a)>>2; {target}[1:0]<=-a;" if partial
            else f"{target}<=-a;")
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      logic signed [3:0] x[4:5][3:2];
      always @(posedge clk) begin {body} end
      always_comb if(x[5][2]==-3) q=123; else q=x[4][3];
    endmodule"""
    machine, plan = _lower(tmp_path, source)
    names = _names(machine)
    assert machine.procedure.types[names["x"]] == types.Array(2, types.Array(2))
    initial = {"clk": 0, "a": 0, "q": 0, "x": [[0, 0], [0, 0]]}
    state = _state(machine, initial, startup=True)
    state, _ = _settle(machine, state)
    _, _, replay = _prepare_clocked_replay(tmp_path, source, plan)
    values = (0, 1, 2, 3, 4, 7, 15, 0)
    actual, native_hits = replay(values)
    for value, expected, reached in zip(values, actual, native_hits, strict=True):
        state, _, _, _, _ = _step(machine, state, 0, clk=0, a=value)
        state, hits = _settle(machine, state)
        state, _, _, _, _ = _step(machine, state, 0, clk=1)
        state, edge_hits = _settle(machine, state)
        assert state[names["q"]] == expected
        assert hits | edge_hits == reached
    goal = _goal(source, plan, "x[5][2]==-3")
    _, baseline_hits = replay((0, 1, 2))
    assert all(goal not in reached for reached in baseline_hits)
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    answer = query_source_interval(machine, env, _state(machine, initial),
        {names["clk"]: 1, names["a"]: 0}, (goal,), mutable_inputs=(names["a"],),
        event_bound=machine.event_bound, timeout_ms=30_000)
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    assert answer.candidate[names["a"]] == 3
    _, confirmed = replay((answer.candidate[names["a"]],))
    assert goal in confirmed[0]


@pytest.mark.eda("slang")
@pytest.mark.solver
def test_array_restore_notifies_only_the_written_cells_consumers(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [7:0] q,r);
      logic [7:0] x[4:5][3:1];
      always @(posedge clk) begin x[4][1]<=a; x[4][1]<=0; end
      always_comb q=x[4][1];
      always_comb r=x[5][3];
    endmodule"""
    machine, _ = _lower(tmp_path, source)
    names = _names(machine)
    initial = {"clk": 0, "a": 0, "q": 0, "r": 0, "x": [[0, 0, 0], [0, 0, 0]]}
    state = _state(machine, initial)
    state, _, _, _, _ = _step(machine, state, 0, clk=1, a=5)
    producer, = [i + 1 for i, flag in enumerate(machine.pending) if state[flag]]
    state, _, _, _, _ = _step(machine, state, producer)
    assert state[names["x"]] == initial["x"] and not any(state[f] for f in machine.pending)
    state, _, _, _, _ = _step(machine, state, machine.commit_action)
    assert state[names["x"]] == initial["x"]
    consumer, = [i for i, flag in enumerate(machine.pending) if state[flag]]
    writes = {name for name, blocking, _ in machine.program.processes[consumer].write_masks if blocking}
    assert names["q"] in writes and names["r"] not in writes


@pytest.mark.eda("slang")
@pytest.mark.solver
def test_whole_array_nba_keeps_old_reads_and_new_shape(tmp_path):
    from .test_rtl_source_batch import _lower as lower_batch

    source = """module M(input clk,input [3:0] a,output logic [3:0] q);
      logic [3:0] x[2];
      always @(posedge clk) begin x<='{a,~a}; q=x[0]; end
    endmodule"""
    batch, _ = lower_batch(tmp_path, source)
    names = {field.name: name for name, field in batch.storage}
    state = {names["x"]: [6, 9], names["q"]: 0}
    post, _, _, error = interp.run_step(batch.procedure,
        {names["a"]: 3, batch.active[0]: True}, state)
    assert not error
    assert post[names["x"]] == [3, 12] and post[names["q"]] == 6


@pytest.mark.eda("slang", "verilator", "make")
@pytest.mark.isolation
@pytest.mark.solver
def test_public_array_nba_baseline_miss_search_and_native_confirmation(tmp_path):
    from behave.sim import api
    from behave.sim.core.goal_provider import RTL_LOCAL_GUIDANCE_POLICY

    from .test_baseline_public_sequence import REFERENCE, _contract

    source = """module M(input clk,rst,in_valid,out_ready,input [7:0] a,
      output in_ready,out_valid,output logic [7:0] y);
      logic [7:0] banks[2];
      assign in_ready=out_ready;
      assign out_valid=in_valid;
      always @(posedge clk) begin
        if(rst) begin banks[0]<=0; banks[1]<=0; end
        else if(in_valid && in_ready) banks[a[0]]<=a;
      end
      always_comb if(banks[1]==173) y=a; else y=a;
    endmodule"""
    contract = _contract()
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": REFERENCE.encode()}, config={"class": "M"})
    path = tmp_path / "candidate.sv"
    path.write_text(source)
    task = api.admit(contract, api.realization_of_rtl_source(source, top="M", contract=contract),
                     reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "rtl"))
    providers = tuple(p for p in handle.generation_providers(reference, workdir=tmp_path / "queries")
                      if p.policy == RTL_LOCAL_GUIDANCE_POLICY)
    assert len(providers) == 1
    result = api.handle_command(handle.command_request(run_directory=tmp_path / "command",
        reference=reference, baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=100),
        providers=providers, search_policy=api.SearchPolicy(time_budget_s=120, max_probes=4, debug=True),
        run_analysis=False))
    assert result.status is api.CommandStatus.EVALUATED, result.failure
    assert result.decision.authoritative and result.decision.passed and result.evaluation.complete
    search, = result.generated.searches
    assert not search.telemetry["refusals"]
    baseline = search.telemetry["baseline_coverage"]
    new_goals = set(result.evaluation.hits) - set(baseline["hits"])
    assert new_goals and new_goals <= set(baseline["unhit"])
    assert len(result.generated.stimuli) > len(result.generated.baseline)
    baseline_members = {seed.identity_bytes for seed in result.generated.baseline}
    assert any(goal in new_goals and hit for run in result.evaluation.results
               if run.stimulus.identity_bytes not in baseline_members and run.stimulus.depth("in") == 100
               for record in run.trace.records for goal, hit in record.hits)
