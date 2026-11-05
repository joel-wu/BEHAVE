"""Worker imports do not eagerly initialize unrelated public services."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("module", [
    "behave.sim.rtl.trace_postprocessing",
    "behave.sim.rtl._tool_job",
    "behave.sim.symbolic.ir",
])
def test_stage_import_then_public_api_keeps_one_identity(module):
    source = Path(__file__).resolve().parents[2] / "src"
    code = f"""
import importlib
import sys
importlib.import_module({module!r})
assert 'behave.sim.api' not in sys.modules
import behave.sim as sim
from behave.sim import api
assert api is sim.api is importlib.import_module('behave.sim.api')
assert sim.PACKAGE_DIR.is_dir()
assert not hasattr(sim, 'not_a_sim_attribute')
namespace = {{}}
exec('from behave.sim import *', namespace)
assert namespace['api'] is api
assert namespace['PACKAGE_DIR'] == sim.PACKAGE_DIR
"""
    result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True,
                            text=True, timeout=30, check=False,
                            env=dict(os.environ, PYTHONPATH=str(source)))
    assert result.returncode == 0, result.stderr
