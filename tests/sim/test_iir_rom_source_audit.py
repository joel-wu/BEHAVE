"""A real source/IR regression retained from the retired ad-hoc census runner."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.dataset


def test_iir_rom_lowering_agrees_with_native_steps():
    from behave.sim.behavior import audit, instrumented
    from behave.sim.behavior.lowering import lower_with_state_ranges

    path = (Path(__file__).resolve().parents[2] / "data/problems/numeric"
            / "numeric_pool_dsp_filter_arm_iir_lattice_q15/behavior.py")
    proc = lower_with_state_ranges(
        path.read_text(encoding="utf-8"), file="behavior.py",
        input_widths=[16], outputs=["out_sample"])
    namespace, _sites, hits = instrumented.load_instrumented(path)
    model = instrumented._model_class(namespace)
    traces = [[
        {"in_sample": value}
        for value in (0, 1, 0x7FFF, 0x8000, 0xFFFF, 0x1234)
    ]]
    assert audit.model_audit(proc, model, traces, hits) == []
