"""Anonymous native storage is compiler-bound, never renamed or zero-filled."""
import json
import sys

import pytest

from behave.sim.rtl.source_boundary_capture import BoundaryCapture, boundaries_of
from behave.sim.rtl.source_edits import Insertion, SourceEdits
from behave.sim.rtl.source_native_layout import needs_layout, prepare_paths, scope_paths
from behave.sim.rtl.source_storage import anonymous_scope_name

from .test_rtl_source_boundary_capture import Signal


def test_native_scope_witness_projects_insertions_and_preserves_unavailable_state():
    source = 'begin\n  int x;\nend'
    edits = SourceEdits(source, (Insertion(0, '// observer\n'),))
    name = anonymous_scope_name({'source_line': 1, 'source_column': 1})
    key = ('child', name, 'x')
    fields = ((key, 8), ('x', 8))
    metadata = {'files': {'a': {'filename': 'native.sv'}, 'b': {'filename': '<built-in>'}}}
    tree = [{'type': 'BEGIN', 'name': 'unnamedblk7', 'unnamed': True, 'loc': 'a,2:1,2:6'},
            {'type': 'BEGIN', 'name': 'irrelevant', 'unnamed': True, 'loc': 'b,2:1,2:6'},
            {'type': 'BEGIN', 'name': 'generated', 'unnamed': True, 'loc': 'a,1:3,1:6'}]
    paths = scope_paths(tree, metadata, edits, fields)
    assert paths == ((key, ('child', 'unnamedblk7', 'x')),)
    dut = {'child': {'unnamedblk7': {'x': Signal(17)}}, 'x': Signal(99)}
    capture = BoundaryCapture(dut, fields, paths=paths)
    capture.observe(0, {'in': (1,)}, {'in': 0})
    assert boundaries_of(capture.as_dict())[0] == {key: 17, 'x': 99}
    # Different specializations cannot silently choose one compiler block.
    tree.append({**tree[0], 'name': 'unnamedblk8'})
    paths = scope_paths(tree, metadata, edits, fields)
    assert paths == ((key, None),)
    capture = BoundaryCapture(dut, fields, paths=paths)
    capture.observe(0, {'in': (1,)}, {'in': 0})
    assert boundaries_of(capture.as_dict())[0] == {key: None, 'x': 99}


def test_named_storage_needs_no_extra_compilation(tmp_path):
    fields = (('x', 8), (('child', 'named', 'x'), 4))
    assert not needs_layout(fields)
    assert prepare_paths(tmp_path, 'M', SourceEdits('', ()), fields,
                         run=lambda *a: pytest.fail('unnecessary compiler invocation')) == ()


def test_layout_failure_is_unavailable_not_a_guessed_scope(tmp_path):
    name = anonymous_scope_name({'source_line': 3, 'source_column': 4})
    fields = (((name, 'x'), 8),)
    assert prepare_paths(tmp_path, 'M', SourceEdits('', ()), fields,
                         run=lambda *a: False) == (((name, 'x'), None),)


def test_generated_parent_scope_is_retained_in_native_path():
    source = 'begin\n  for (;;) begin\n  end\nend'
    edits = SourceEdits(source, (Insertion(6, '// observer\n'),))
    outer = anonymous_scope_name({'source_line': 1, 'source_column': 1})
    inner = anonymous_scope_name({'source_line': 2, 'source_column': 12})
    key = ('child', outer, inner, 'x')
    tree = {'type': 'BEGIN', 'name': 'unnamedblk1', 'unnamed': True,
            'loc': 'a,1:1,1:6', 'body': [
                {'type': 'BEGIN', 'name': 'unnamedblk2', 'unnamed': True,
                 'loc': 'a,2:1,2:6', 'body': [
                     {'type': 'BEGIN', 'name': 'unnamedblk3', 'unnamed': True,
                      'loc': 'a,3:12,3:17'}]}]}
    metadata = {'files': {'a': {'filename': 'native.sv'}}}
    assert scope_paths(tree, metadata, edits, ((key, 8),)) == (
        (key, ('child', 'unnamedblk1', 'unnamedblk2', 'unnamedblk3', 'x')),)
    wrong = ('child', 'different_parent', inner, 'x')
    assert scope_paths(tree, metadata, edits, ((wrong, 8),)) == ((wrong, None),)


def test_missing_optional_layout_does_not_break_native_preparation(tmp_path):
    name = anonymous_scope_name({'source_line': 3, 'source_column': 4})
    fields = (((name, 'x'), 8),)
    assert prepare_paths(tmp_path, 'M', SourceEdits('', ()), fields,
                         run=lambda *a: True) == (((name, 'x'), None),)


def test_deep_compiler_expressions_preserve_the_exact_native_scope(tmp_path):
    source = 'begin int x; end'
    edits = SourceEdits(source, ())
    name = anonymous_scope_name({'source_line': 1, 'source_column': 1})
    key = ('child', name, 'x')
    tree = {'type': 'BEGIN', 'name': 'unnamedblk7', 'unnamed': True, 'loc': 'a,1:1,1:6'}
    # A realistic compiler tree can be deeper than Python's default decoder
    # and walker. The nested expressions introduce no extra HDL scopes.
    previous_limit = sys.getrecursionlimit()
    depth = previous_limit + 100
    (tmp_path / 'layout.json').write_text('[' * depth + json.dumps(tree) + ']' * depth)
    (tmp_path / 'layout').mkdir()
    (tmp_path / 'layout/Vtop.tree.meta.json').write_text(json.dumps({
        'files': {'a': {'filename': 'native.sv'}}}))
    assert prepare_paths(tmp_path, 'M', edits, ((key, 8),), run=lambda *a: True) == (
        (key, ('child', 'unnamedblk7', 'x')),)
    assert sys.getrecursionlimit() == previous_limit


def test_optional_decoder_exhaustion_remains_explicitly_unavailable(tmp_path, monkeypatch):
    from behave.sim.rtl import source_native_layout

    name = anonymous_scope_name({'source_line': 1, 'source_column': 1})
    key = (name, 'x')
    (tmp_path / 'layout.json').write_text('[]')
    previous_limit = sys.getrecursionlimit()

    def exhausted(_text):
        raise RecursionError('compiler tree exceeds the configured EDA limit')

    monkeypatch.setattr(source_native_layout.json, 'loads', exhausted)
    assert prepare_paths(tmp_path, 'M', SourceEdits('', ()), ((key, 8),),
                         run=lambda *a: True) == ((key, None),)
    assert sys.getrecursionlimit() == previous_limit
