from behave.ppa.eda import sta_script
from behave.ppa.timing_feedback import critical_paths
from behave.ppa.workflow import eda_feedback
from test_ppa import experiment


def report(kind='max', slack=-17.772427, rich=True):
    header = 'Fanout Cap Slew Delay Time Description' if rich else 'Delay Time Description'
    values = '512 2.0 60.0 ' if rich else ''
    return f'''Startpoint: state (rising edge-triggered flip-flop clocked by clk)
Endpoint: input_buffer (rising edge-triggered flip-flop clocked by clk)
Path Group: clk
Path Type: {kind}
{header}
  0.1 0.1 ^ state/Q (ff)
  {values}34.506245 35.552315 ^ _4329_/Y (nor3)
  0.0 35.552315 ^ input_buffer/DE (ff)
  35.552315 data arrival time
  17.779890 data required time
  {slack} slack (VIOLATED)
'''


def test_feedback_selects_worst_paths_and_reports_bottleneck():
    paths = critical_paths(report(slack=1) + report() + report(kind='min', slack=.1))
    assert len(paths) == 2
    assert paths[0]['slack_ns'] == -17.772427
    pin = paths[0]['largest_increments'][0]
    assert pin['pin'] == '_4329_/Y'
    assert pin['fanout'] == 512
    assert pin['delay_ns'] == 34.506245
    assert paths[0]['highest_fanout'] == pin


def test_legacy_report_still_has_diagnostics_without_inventing_fanout():
    path, = critical_paths(report(rich=False))
    assert path['largest_increments'][0]['delay_ns'] == 34.506245
    assert 'highest_fanout' not in path
    assert critical_paths('compilation failed') == []
    assert critical_paths(report().replace('-17.772427 slack', 'nan slack')) == []


def test_public_feedback_uses_native_log_and_keeps_complete_file():
    log = report()
    public, files = eda_feedback(dict(status='measured', area_um2=42,
        timing=dict(status='violated'), log=log, evidence={'PRIVATE': 'handle'}), prefix='eda/test')
    assert public['critical_paths'][0]['highest_fanout']['fanout'] == 512
    assert 'fanout 512' in files[0].data.decode()
    assert files[1].data.decode() == log
    assert 'PRIVATE' not in repr((public, files))


def test_native_report_requests_available_fields_without_changing_constraints():
    script = sta_script('dut', experiment().timing)
    assert '-fields {capacitance slew fanout}' in script
    assert '-no_line_splits' in script
    assert 'BEHAVE_PPA_SETUP_NS' in script


def test_femtofarad_library_feedback_keeps_public_picofarad_units():
    public, files = eda_feedback(dict(status='measured', area_um2=42,
        timing=dict(status='violated'), log=report(), report_units={'time': 'ns', 'capacitance': 'fF'}),
        prefix='eda/test')
    assert public['critical_paths'][0]['highest_fanout']['capacitance_pf'] == 0.002
    assert 'load 0.002 pF' in files[0].data.decode()
    assert files[1].data.decode() == report()
