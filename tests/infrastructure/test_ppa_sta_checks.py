"""Execute the STA diagnostic adapter, including mixed/unknown endpoints."""
import shutil
import subprocess

import pytest

from behave.ppa.sta_checks import SETUP_CHECKS
from behave.ppa.eda import timing_result


@pytest.mark.parametrize('pins,expected', [
    ('low high', 'met'), ('dynamic', 'unresolved'), ('missing', 'unresolved'),
    ('low dynamic high', 'unresolved'),
])
def test_only_proven_constant_endpoints_are_exempt(pins, expected):
    tcl = shutil.which('tclsh')
    if tcl is None:
        pytest.skip('Tcl interpreter unavailable')
    script = '''namespace eval sta {
      proc check_timing_cmd {args} {
        return [list [linsert $::pins 0 "Warning: There are [llength $::pins] unconstrained endpoints."]]
      }
      proc find_pin {name} { if {$name eq "missing"} {return NULL}; return $name }
      proc pin_sim_logic_value {pin} {
        if {$pin eq "low"} {return 0}
        if {$pin eq "high"} {return 1}
        return X
      }
    }
    ''' + f'set pins {{{pins}}}\n' + SETUP_CHECKS
    result = subprocess.run([tcl], input=script, text=True, capture_output=True, check=True)
    assert not result.stderr, result.stderr
    output = result.stdout + 'BEHAVE_PPA_SETUP_NS 1.0\nBEHAVE_PPA_HOLD_NS 0.1\n'
    assert timing_result(output)['status'] == expected, output


def test_constant_exemption_does_not_hide_another_sta_check():
    tcl = shutil.which('tclsh')
    if tcl is None:
        pytest.skip('Tcl interpreter unavailable')
    script = '''namespace eval sta {
      proc check_timing_cmd {args} {
        return {{"Warning: There is 1 unclocked register/latch pin." latch/CLK}}
      }
    }
    ''' + SETUP_CHECKS
    result = subprocess.run([tcl], input=script, text=True, capture_output=True, check=True)
    assert not result.stderr, result.stderr
    assert 'unclocked' in result.stdout and 'latch/CLK' in result.stdout
    assert timing_result(result.stdout)['status'] == 'unresolved'
