"""Compose source activations and a single deferred-write commit in shared IR.

This is the active/NBA *batch* primitive of the source scheduler, not the whole
RTL step. The scheduler still supplies activation flags, propagates ports and
repeats event regions until quiescence. In particular, it may not run every
combinational process once and call that a settled source execution.
"""

from dataclasses import dataclass

from ..symbolic import ir, typecheck as types
from ..symbolic import ranges
from ..symbolic.procedure_contract import ProcedureSignature
from ..core.contract import FiniteType
from ..core.refusal import Refusal, Refused
from ..core.value_type import ValueType
from .source_effects import accesses
from .source_ir import SourceProcedure, _sequence
from .source_program import lower_source_program
from .source_storage import SourceStorage


@dataclass(frozen=True, slots=True)
class SourceBatch:
    procedure: ir.Procedure
    processes: tuple[SourceProcedure, ...]
    # Scheduler input flag for each process, in the same order as processes.
    active: tuple[str, ...]
    storage: tuple[tuple[str, SourceStorage], ...]
    input_ranges: dict
    # Source target -> single-record output of bits changed in this NBA commit.
    # The future event scheduler tests each subscriber's resolved read range.
    commit_changes: tuple[tuple[str, str], ...]

    @property
    def occurrences(self):
        return tuple(item for process in self.processes for item in process.occurrences)


def _effects(process):
    """A sufficient order-independence check, not a new reachability analysis."""
    storage = {name for name, _ in process.storage}
    read, blocking = accesses(process.activation, storage)
    deferred = {write.target for write in process.deferred}
    return read, blocking, deferred


def _check_commuting(processes):
    # Index by actual touched storage, rather than comparing every process pair.
    readers, blocking_writers, writers = {}, {}, {}
    for process in processes:
        read, blocking, deferred = _effects(process)
        for touched, owners in ((read, blocking_writers), (blocking, readers),
                                (blocking | deferred, writers)):
            for name in sorted(touched):
                previous = owners.get(name)
                if previous is not None:
                    raise Refused(Refusal("rtl-source-batch-order-unproved",
                        "coactive source processes need an explicit ordering or an independence proof",
                        contexts=(previous, process.context.key), storage=name))
        for touched, owners in ((read, readers), (blocking, blocking_writers),
                                (blocking | deferred, writers)):
            for name in touched:
                owners.setdefault(name, process.context.key)


def lower_source_batch(source, document, plan, *, filename, contexts=None):
    """Lower one explicitly selected set of possibly coactive processes.

    No source goal is inferred, pruned or renamed. Only compiler temporaries and
    storage names are allocated here. Persistent storage is shared across the
    activations; their deferred writes are committed after all active commands.
    """
    program = lower_source_program(source, document, plan, filename=filename, contexts=contexts)
    processes, fields = program.processes, program.fields
    input_types = {name: program.kinds[name].signature_type for name, field in fields.items() if field.input}
    output_types = []
    _check_commuting(processes)
    storage = tuple(fields.items())
    state = [name for name, field in storage if not field.input]
    inputs = [name for name, field in storage if field.input]
    active = tuple(f"active{index}" for index in range(len(processes)))
    inputs.extend(active)
    input_types.update((flag, ValueType.boolean()) for flag in active)
    types_ = {flag: types.BOOLEAN for flag in active}
    domains = {flag: ranges.Range(0, 1) for flag in active}
    scratch = 0

    def temp(*, kind=types.INTEGER):
        nonlocal scratch
        name = f"batch_tmp{scratch}"
        scratch += 1
        types_[name] = kind
        return name

    initialization, activations, commits, changes = [], [], [], []
    for flag, process in zip(active, processes):
        types_.update(process.procedure.types)
        domains.update(process.procedure.state_ranges)
        for write in process.deferred:
            changed = f"commit{len(changes)}"
            types_[changed] = write.array.ir_type if write.array is not None else types.INTEGER
            output_types.append((changed, write.mask_kind.signature_type if write.array is not None
                                 else FiniteType(write.width)))
            changes.append((write.target, changed))
            initialization.extend(write.initialize(temp))
            commits.extend(write.commit(changed, temp))
        activations.append(ir.If(ir.Var(flag), process.activation, ir.Skip()))
    outputs = [changed for _, changed in changes]
    # One batch returns one change-summary record. Use the shared IR's explicit
    # singleton output buffers, keeping scalar commit values available to the
    # scheduler; these are not the RTL task's transaction outputs.
    output_vars = {changed: f"{changed}_buffer" for changed in outputs}
    types_.update((buffer, types.Array(1, types_[changed])) for changed, buffer in output_vars.items())
    pack = [ir.Assign(buffer, ir.Op("arraylit", ir.Var(changed)))
            for changed, buffer in output_vars.items()]
    body = _sequence([*initialization, *activations, *commits, *pack])
    locals_ = [name for name in types_ if name not in {*state, *inputs}]
    procedure = ir.Procedure(state, inputs, locals_, outputs, body, types=types_,
        output_vars=output_vars, output_capacity=1,
        signature=ProcedureSignature(tuple((name, input_types[name]) for name in inputs),
                                     tuple(output_types)))
    procedure.state_ranges = {name: domains[name] for name in state}
    return SourceBatch(procedure, tuple(processes), active, storage,
                       {name: domains[name] for name in inputs}, tuple(changes))
