"""Physical scopes, not lexical names, own cached elaboration parameters."""

import pytest

from behave.sim.rtl.shared_frontend import parse_observation_source
from .test_rtl_observation_frontend import _installed


@pytest.mark.eda("slang")
def test_parameter_cache_preserves_instances_shadowing_and_genvars(tmp_path):
    source = """module leaf #(parameter signed [7:0] P=-2)(input a, output [5:0] q);
      assign q[0] = a ^ P[0];
      assign q[1] = a ^ P[1];
      for(genvar i=0;i<2;i++) begin: lane
        localparam signed [7:0] P=i+3;
        assign q[2+2*i] = a ^ P[0];
        assign q[3+2*i] = a ^ P[1];
      end
    endmodule
    module M(input a, output [11:0] q);
      leaf #(-2) left(a,q[5:0]); leaf #(7) right(a,q[11:6]);
    endmodule"""
    (tmp_path / "design.sv").write_text(source)
    _, rows = parse_observation_source(tmp_path, "M", run=_installed)
    assert len(rows) == 12
    scopes = {}
    for row in rows:
        parameters = {item["reference"].replace("\\", "").strip():
                      (item["width"], int(item["bits"], 16))
                      for item in row["instance_parameters"]}
        scopes.setdefault(row["scope"], []).append(parameters)
    assert len(scopes) == 6
    for name, parameters in scopes.items():
        assert len(parameters) == 2 and parameters[0] == parameters[1]
        if ".lane[" in name:
            index = int(name.rsplit("[", 1)[1][:-1])
            assert parameters[0]["P"] == (8, index + 3)
            assert parameters[0]["i"][1] == index
        else:
            assert parameters[0]["P"] == (8, 254 if name.endswith("left") else 7)


@pytest.mark.eda("slang")
def test_parameterless_processes_are_all_retained(tmp_path):
    count = 256
    source = "module M(input a, output y);\n"
    source += "\n".join(f"wire n{i};" for i in range(count))
    source += "\nassign n0=a;\n"
    source += "\n".join(f"assign n{i}=~n{i-1};" for i in range(1, count))
    source += f"\nassign y=n{count-1}; endmodule"
    (tmp_path / "design.sv").write_text(source)
    _, rows = parse_observation_source(tmp_path, "M", run=_installed)
    assert len(rows) == count + 1
    assert all(row["instance_parameters"] == [] for row in rows)
    assert all(len(row["reads"]) == 1 for row in rows)
    assert len({(row["start"], row["end"]) for row in rows}) == count + 1
