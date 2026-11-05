"""Generated commit summaries agree with ordered, masked four-state writes."""

import re
import subprocess
from itertools import product

import pytest

from behave.sim.rtl.source_deferred_events import DeferredRead, _pack_projection

from .test_rtl_source_plan import _run

pytestmark = pytest.mark.eda("iverilog", "vvp")


@pytest.mark.parametrize('width', (16, 128, 252))
def test_packed_projection_zero_extends_and_preserves_four_states(tmp_path, width):
    declarations, statements, (packed,) = _pack_projection(('value',), 0, 'packed', width=width)
    source = ('module tb; logic signed [3:0] value; ' + ''.join(declarations)
              + ' initial begin value=4\'bx1z1; ' + ''.join(statements)
              + f' if ({packed} !== {{{{{width-4}{{1\'b0}}}},4\'bx1z1}}) $fatal(1,"incorrect packed conversion");'
              + ' $display("OK"); $finish; end endmodule')
    (tmp_path / 'packed.sv').write_text(source)
    _run(tmp_path, 'iverilog', '-g2012', '-s', 'tb', '-o', 'packed', 'packed.sv')
    run = subprocess.run(['vvp', 'packed'], cwd=tmp_path, capture_output=True,
                         text=True, check=True, timeout=10)
    assert 'OK' in run.stdout


def _icarus_task_wrappers(declarations):
    """Four-state body oracle; Icarus lacks function output/ref arguments.

    All test operands are distinct and helpers do not suspend. Copy-back has
    the same externally visible result here; native tests use the real refs.
    """
    declarations = declarations.replace("const ref ", "input ").replace("ref ", "inout ")
    declarations = re.sub(
        r'function automatic bit \[1:0\] (__behave_native_history_update_\d+)\(',
        r'function automatic void \1(output bit [1:0] next_commit,', declarations)
    declarations = re.sub(r'return (\{batch, varied \| [^;]+);',
                          r'next_commit=\1;', declarations)
    return re.sub(r'\bfunction automatic void\b(.*?)\bendfunction\b',
                  r'task automatic\1endtask', declarations, flags=re.DOTALL)


def test_generated_summary_matches_masked_commits_and_late_rebase(tmp_path):
    queue = DeferredRead("history", 2)
    commands, expected = [], []
    # Distinguish all four states, untouched bits, cancelling writes, no-change
    # batches, and a precommit value different from the enqueue-time value.
    for before, precommit, first, last, masks in product(
            ("00", "11"), ("00", "1x", "z1"),
            ("00", "01", "1x", "z1"), ("00", "11", "1x", "z1"),
            product((0, 1, 2, 3), repeat=2)):
        commands.extend((
            f"mask=2'd{masks[0]}; value=2'b{first}; before_commit=2'b{before};",
            queue.enqueue("mask", "value", "before_commit"),
            f"mask=2'd{masks[1]}; value=2'b{last};",
            queue.enqueue("mask", "value", "before_commit"),
            queue.rebase(f"2'b{precommit}"),
            '#1; $display("COMMIT %0d %0d", history_commit[1], history_commit[0]);',
        ))
        current, changed = list(precommit), False
        for mask, value in zip(masks, (first, last), strict=True):
            for bit in range(2):
                if mask >> bit & 1:
                    position = 1 - bit
                    changed |= current[position] != value[position]
                    current[position] = value[position]
        expected.append((1 - len(expected) % 2, int(changed)))
    # Icarus rejects NBAs and output/inout ports in functions. Use task wrappers for its four-state
    # body oracle; both run immediately when called from this initial block.
    # The actual void-function declarations are separately compiled and run by
    # the original/observed native helper-NBA and scheduling regressions.
    # The update helper returns the next commit while mutating only its ref
    # arguments. Icarus needs the same body as a task with an output argument.
    declarations = _icarus_task_wrappers(queue.declarations)
    commands = [command.replace('history_commit <= __behave_native_history_update_2(',
        '__behave_native_history_update_2(next_commit,') +
        (' history_commit<=next_commit;' if command.startswith('history_commit <=') else '')
        for command in commands]
    source = 'module tb; logic [1:0] mask,value,before_commit; bit [1:0] next_commit;' + declarations + 'initial begin ' + '\n'.join(commands)
    source += "$finish; end endmodule"
    (tmp_path / "queue.sv").write_text(source)
    _run(tmp_path, "iverilog", "-g2012", "-s", "tb", "-o", "queue", "queue.sv")
    result = subprocess.run(["vvp", "queue"], cwd=tmp_path, capture_output=True,
                            text=True, check=True, timeout=30)
    actual = [tuple(map(int, line.split()[1:])) for line in result.stdout.splitlines()
              if line.startswith("COMMIT ")]
    assert actual == expected
