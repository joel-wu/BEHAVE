"""Pure source-map tests: byte coordinates, insertion boundaries and closed input."""

from dataclasses import FrozenInstanceError

import pytest

from behave.sim.rtl.source_edits import Insertion, Replacement, SourceEdits


@pytest.mark.parametrize("data", [b"", b"\n", b"\n\n", b"\r\n", b"\r", b"abc",
                                 "π\r\nα;\n".encode(), bytes(range(256)) * 4])
def test_line_offsets_match_exact_byte_scan(data):
    from behave.sim.rtl.source_edits import _lines

    assert _lines(data) == (0, *(i + 1 for i, value in enumerate(data) if value == 10))


def test_insertion_map_is_exact_and_rejects_generated_interiors():
    edits = SourceEdits("abc\ndef", (Insertion(1, "XYZ"), Insertion(1, "!"), Insertion(5, "++")))
    assert edits.text == "aXYZ!bc\nd++ef"
    expected = {0: 0, 1: 1, 4: 1, 5: 1, 6: 2, 7: 3, 8: 4, 9: 5, 11: 5, 12: 6, 13: 7}
    for offset, original in expected.items():
        assert edits.source_offset(offset) == original
    for offset in (2, 3, 10, -1, 14, True, 1.0):
        with pytest.raises(ValueError):
            edits.source_offset(offset)
    assert edits.source_position(2, 4) == (2, 2)
    with pytest.raises(ValueError, match="outside its line"):
        edits.source_position(1, 10)
    with pytest.raises(FrozenInstanceError):
        edits.source = "replaced"


def test_utf8_crlf_and_eof_are_byte_boundaries_not_python_character_indices():
    source = "// π\r\nα;\n"
    edits = SourceEdits(source, (Insertion(7, "generated\n"), Insertion(len(source.encode()), "end")))
    assert edits.source_position(3, 3) == (2, 3)  # after the two-byte alpha
    assert edits.source_position(4, 1) == (3, 1)
    assert edits.source_position(4, 4) == (3, 1)
    with pytest.raises(ValueError, match="UTF-8"):
        SourceEdits(source, (Insertion(8, "split-alpha"),))


@pytest.mark.parametrize("items", [
    (Insertion(-1, "x"),), (Insertion(4, "x"),),
    (Insertion(2, "x"), Insertion(1, "y")),
    (Insertion(True, "x"),), (Insertion(0, ""),),
])
def test_invalid_edits_fail_closed(items):
    with pytest.raises((TypeError, ValueError)):
        SourceEdits("abc", items)


def test_large_map_keeps_compact_edit_metadata():
    source = "x" * 100000
    edits = SourceEdits(source, tuple(Insertion(i, "12345") for i in range(0, len(source), 100)))
    assert len(edits._spans) == 1000
    assert edits.source_offset(len(edits.text)) == len(source)
    assert SourceEdits(source, ()).text == source


def test_replacement_and_callback_insertions_share_original_byte_coordinates():
    source = "// π\nalways_comb q=a;\n"
    first = source.encode().index(b"always_comb")
    last = first + len(b"always_comb")
    body = source.encode().index(b"q=a;")
    edits = SourceEdits(source, (Insertion(body, "begin hit(); "),
                                Insertion(body + 4, " end @(a); end")),
                        (Replacement(first, last, "initial forever begin"),))
    assert edits.text == "// π\ninitial forever begin begin hit(); q=a; end @(a); end\n"
    generated = edits.text.encode()
    mapped_body = generated.index(b"q=a;")
    for relative in range(5):
        assert edits.source_offset(mapped_body + relative) == body + relative
    assert edits.source_offset(first) == first
    assert edits.source_offset(first + len(b"initial forever begin")) == last
    with pytest.raises(ValueError, match="no original source coordinate"):
        edits.source_offset(first + 2)
    assert edits.source_offset(len(generated)) == len(source.encode())


def test_insertions_at_replacement_boundaries_keep_stable_order():
    edits = SourceEdits("abc", (Insertion(0, "<"), Insertion(1, ">")),
                        (Replacement(0, 1, "XYZ"), Replacement(1, 3, "Z")))
    assert edits.text == "<XYZ>Z"
    assert [edits.source_offset(i) for i in (0, 1, 4, 5, 6)] == [0, 0, 1, 1, 3]


def test_replacement_cannot_consume_another_edit_or_split_unicode():
    with pytest.raises(ValueError, match="overlaps"):
        SourceEdits("abc", (Insertion(1, "x"),), (Replacement(0, 2, "y"),))
    with pytest.raises(ValueError, match="UTF-8"):
        SourceEdits("π", (), (Replacement(0, 1, "x"),))


def test_compiler_replacements_project_all_hierarchy_source_fields():
    from behave.sim.rtl.source_replacements import SourceReplacements

    source = "abc XYZ def\n"
    normalized = SourceReplacements(source, (Replacement(4, 7, "LONG_REPLACEMENT"),))
    before = f"design.sv:1.1-1.{len(normalized.text)}"
    after = f"design.sv:1.1-1.{len(source)}"
    text = "".join(f'  attribute \\{name} "{before}"\n'
                   for name in ("src", "cell_src", "module_src", "note"))
    projected = normalized.rtlil(text)
    for name in ("src", "cell_src", "module_src"):
        assert f'attribute \\{name} "{after}"' in projected
    assert f'attribute \\note "{before}"' in projected
