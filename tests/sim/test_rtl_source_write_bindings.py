"""Source write references are compiler-bound per physical instance."""

import pytest

from behave.sim.rtl.shared_frontend import parse_observation_source
from behave.sim.rtl.source_declarations import declare_source
from behave.sim.rtl.source_goals import manifest_of
from behave.sim.rtl.source_plan import plan_source

from .test_rtl_observation_frontend import _installed

pytestmark = pytest.mark.eda("slang")


@pytest.mark.parametrize('control,stationary', [
    ('a', True), ('a[0]', True), ('a[3:0] or b', True),
    ('a+b', False), ('a[b]', False), ('posedge a[0]', False),
    ('a iff b[0]', False), ('a or negedge b[0]', False),
])
def test_value_event_certificate_never_widens_expression_dependencies(tmp_path, control, stationary):
    source = f'module M(input [7:0] a,b,output logic q); always @({control}) q=a[0]; endmodule'
    (tmp_path / 'design.sv').write_text(source)
    (_, _), sensitivity = parse_observation_source(tmp_path, 'M', run=_installed)
    row, = sensitivity
    assert row['stationary_value_events'] is stationary


def test_write_bindings_cover_each_instance_without_absolute_first_instance_paths(tmp_path):
    source = """module producer(input a);
      bit x;
      always_comb begin x=a; x=0; end
    endmodule
    module cell_model(input a,output logic q);
      producer writer(a);
      always_comb q=writer.x;
    endmodule
    module M(input a,b,output q,r);
      cell_model low(a,q),high(b,r);
    endmodule"""
    (tmp_path / "design.sv").write_text(source)
    manifests = []
    for _ in range(2):
        (cst, ast), sensitivity = parse_observation_source(tmp_path, "M", run=_installed)
        assert {row["scope"] for row in sensitivity} == {
            "M.low", "M.low.writer", "M.high", "M.high.writer"}
        subscribed = [row for row in sensitivity if row["lexical_scope"] == "cell_model"]
        assert len(subscribed) == 2
        assert len({row["reads"][0]["symbol_id"] for row in subscribed}) == 2
        for row in subscribed:
            assert row["triggers"] == ["writer.x"]
            read, = row["reads"]
            assert [source[write["start"]:write["end"]] for write in read["write_references"]] == ["x", "x"]
            # The compiler validates this upward module-type qualification in
            # both instances; it does not point every writer at M.low.
            assert len({write["scope_reference"] for write in read["write_references"]}) == 1
            assert all("M.low" not in write["scope_reference"] and "M.high" not in write["scope_reference"]
                       for write in read["write_references"])
        plan = plan_source(source, cst, semantic_document=ast, sensitivity_document=sensitivity)
        assert not plan.native_issues
        declared = declare_source(source, ast, filename="design.sv")
        manifests.append(manifest_of(declared, filename="design.sv").as_dict())
    assert manifests[0] == manifests[1]


def test_elaboration_constants_are_not_runtime_activation_dependencies(tmp_path):
    source = """module M(input [7:0] a,output logic [31:0] q);
      localparam int WIDTH=16;
      for(genvar k=0;k<2;k++) begin: consumer
        always_comb q[k*WIDTH+:WIDTH]=a;
      end
    endmodule"""
    (tmp_path / "design.sv").write_text(source)
    (_, ast), sensitivity = parse_observation_source(tmp_path, "M", run=_installed)
    assert len(sensitivity) == 2
    assert all({read["symbol"] for read in row["reads"]} == {"M.a"} for row in sensitivity)
    # Filtering scheduling dependencies must not remove declaration contexts.
    declared = declare_source(source, ast, filename="design.sv")
    assert {context.scope for context in declared.contexts} == {
        ("M", "consumer[0]"), ("M", "consumer[1]")}


def test_shared_array_writer_family_has_one_valid_reference_per_subscriber(tmp_path):
    source = '''module M(input a,output logic [2:0] q);
      bit x[3];
      for(genvar g=0;g<3;g++) begin: lane
        if(g>0) begin: tail
          always_comb q[g]=x[g-1];
        end
        if(g<2) begin: writer
          always @(posedge a) x[g]<=a;
        end
      end
    endmodule'''
    (tmp_path / "design.sv").write_text(source)
    (_, _), sensitivity = parse_observation_source(tmp_path, "M", run=_installed)
    consumers = [row for row in sensitivity if row["scope"].endswith(".tail")]
    assert len(consumers) == 2
    for row in consumers:
        read, = row["reads"]
        writes = read["write_references"]
        assert len(writes) == 2  # two physical writers at the same source site
        assert len({write["scope_reference"] for write in writes}) == 1
        reference = writes[0]["scope_reference"]
        assert "\\g " not in reference
        assert row["scope"].removeprefix("M.") in reference
