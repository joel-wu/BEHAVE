"""Verilator, not an Eval point parser, owns line mapping and merging."""

import pytest

from behave.sim.cocotb import _export_line_coverage, _line_counts


def test_lcov_counts_are_lines_not_execution_counts():
    report = b"TN:verilator_coverage\nSF:a.sv\nDA:3,50\nDA:4,0\nend_of_record\nSF:b.sv\nDA:3,7\nend_of_record\n"
    assert _line_counts(report) == (2, 3)
    assert _line_counts(b"TN:verilator_coverage\n") == (0, 0)


def test_lcov_locations_are_not_opened(tmp_path, monkeypatch):
    from pathlib import Path

    source = tmp_path / "source.sv"
    source.write_text("not input to the coverage counter\n")
    linked = tmp_path / "linked.sv"
    linked.symlink_to(source)
    report = f"SF:{linked}\nDA:3,1\nDA:4,0\nend_of_record\n".encode()

    def no_open(*args, **kwargs):
        raise AssertionError("line counts must come only from the exported report")

    with monkeypatch.context() as patch:
        patch.setattr("builtins.open", no_open)
        patch.setattr(Path, "open", no_open)
        patch.setattr("os.open", no_open)
        assert _line_counts(report) == (1, 2)


@pytest.mark.parametrize("returncode,timed_out,report", [
    (1, False, b"TN:verilator_coverage\nDA:3,1\n"),
    (0, True, b"TN:verilator_coverage\nDA:3,1\n"),
    (0, False, b""),
])
def test_failed_or_missing_export_is_not_reported_as_zero(tmp_path, monkeypatch, returncode, timed_out, report):
    from behave.sim.rtl import tool_isolation

    from .test_rtl_tool_isolation import _command_row, _completed

    row = _command_row("submitted-tb:coverage", returncode=returncode, timed_out=timed_out)
    result = _completed(commands=(row,), outputs={"coverage.info": report})
    monkeypatch.setattr(tool_isolation, "run_workspace", lambda *a, **kw: result)
    execution, counts = _export_line_coverage(tmp_path / "export", b"raw", 65536)
    assert execution is result and counts is None


@pytest.mark.isolation
@pytest.mark.eda("verilator")
def test_native_export_merges_sites_instances_and_expands_line_ranges(tmp_path):
    def point(file, line, hits, *, column=1, hierarchy="top", lines=""):
        fields = {"f": file, "l": str(line), "n": str(column), "page": "v_line/test", "t": "line",
                  "h": hierarchy, "S": lines}
        return ("C '" + "".join("\x01" + key + "\x02" + value for key, value in fields.items())
                + "' " + str(hits) + "\n").encode()

    raw = b"# SystemC::Coverage-3\n" + b"".join((
        point("a.sv", 3, 0), point("a.sv", 3, 5, column=2),
        point("a.sv", 3, 1, hierarchy="other"), point("a.sv", 7, 0),
        point("b.sv", 3, 2, lines="3-5,8"),
    ))
    execution, counts = _export_line_coverage(tmp_path / "export", raw, 65536)
    assert counts == (5, 6), execution.as_dict()
    report = execution.outputs["coverage.info"]
    assert b"SF:a.sv\n" in report and b"SF:b.sv\n" in report
    assert len([row for row in report.splitlines() if row.startswith(b"DA:")]) == 6


@pytest.mark.isolation
@pytest.mark.eda("verilator")
def test_native_export_with_no_instrumented_lines(tmp_path):
    execution, counts = _export_line_coverage(tmp_path / "export", b"# SystemC::Coverage-3\n", 65536)
    assert counts == (0, 0), execution.as_dict()


@pytest.mark.isolation
@pytest.mark.eda("verilator")
def test_native_export_does_not_truncate_uncovered_lines(tmp_path):
    raw = b"# SystemC::Coverage-3\n" + b"".join(
        f"C '\x01f\x02rtl.sv\x01l\x02{line}\x01page\x02v_line/test\x01t\x02line' 0\n".encode()
        for line in range(1, 66)
    )
    execution, counts = _export_line_coverage(tmp_path / "export", raw, 65536)
    assert counts == (0, 65), execution.as_dict()
    rows = execution.outputs["coverage.info"].splitlines()
    assert {row for row in rows if row.startswith(b"DA:")} == {
        f"DA:{line},0".encode() for line in range(1, 66)
    }
