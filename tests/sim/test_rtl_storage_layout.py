"""Compiler coordinate mapping is complete, bit-exact and independent of names."""

from __future__ import annotations

import copy


from behave.sim.rtl._yosys_state_ownership import source_state_smt2


def _documents():
    module = {
        "ports": {"clk": {"direction": "input", "bits": [1]},
                  "rst": {"direction": "input", "bits": [2]}},
        "cells": {"ff": {
            "type": "$adff", "parameters": {"ARST_POLARITY": "1"},
            "connections": {"CLK": [1], "ARST": [2], "Q": [10, 11, 12, 13]}}},
        "netnames": {"state": {"bits": [10, 12, 11, 13], "hide_name": 0}},
    }
    before = {"modules": {"top": module}}
    after = copy.deepcopy(before)
    lowered = after["modules"]["top"]
    lowered["cells"]["ff"]["type"] = "$dff"
    lowered["cells"]["ff"]["connections"]["Q"] = [20, 21, 22, 23]
    lowered["netnames"]["arbitrary_storage_name"] = {"bits": [20, 21, 22, 23]}
    smt2 = """(declare-sort |top_s| 0)
(declare-fun |top_n arbitrary_storage_name| (|top_s|) (_ BitVec 4))
; yosys-smt2-register arbitrary_storage_name 4
"""
    return before, after, smt2


def test_contiguous_storage_keeps_one_accessor_not_per_bit_concatenation():
    before, after, text = _documents()
    before["modules"]["top"]["netnames"]["state"]["bits"] = [10, 11, 12, 13]
    result = source_state_smt2(before, after, text, "top")
    assert "concat" not in result and "extract" not in result
    assert "(_ BitVec 4) (|top_n arbitrary_storage_name| state))" in result

