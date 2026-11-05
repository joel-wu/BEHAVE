"""Owner-driven receipt collection must not turn execution errors into success."""

import os

import pytest

from tests.isolation import test_status_lifetime as lifetime

handle = lifetime.handle
publish = lifetime.publish


@pytest.mark.parametrize('status', ['timed-out', 'cpu-limit', 'memory-limit'])
def test_reaped_launcher_keeps_supervisor_resource_cause(handle, status):
    process, writer = handle
    # The namespace launcher exits zero even when its supervised worker fails.
    publish(writer, {'kind': 'finished', 'status': status})
    assert process.final_status == status
    assert process.status_refusal is None
    assert process.wait(timeout=0) < 0
    process.close(deadline=0)
    assert process.final_status == status


@pytest.mark.parametrize('payload', [
    b'not-json\n',
    b'{"kind":"ready","pid":17}\n',
    b'{"kind":"finished","status":"completed","exit_code":1}\n',
])
def test_malformed_receipt_remains_verifier_error_after_successful_reap(handle, payload):
    process, writer = handle
    os.write(writer, payload)
    assert process.final_status == 'refused'
    failure = process.status_refusal
    assert failure.code == 'isolation-supervisor-failed'
    assert process.wait(timeout=0) < 0
    process.close(deadline=0)
    assert process.status_refusal is failure
    assert process.final_status == 'refused'


def test_terminal_cleanup_refusal_keeps_original_execution_and_cleanup(handle):
    process, writer = handle
    publish(writer, {
        'kind': 'refused', 'code': 'isolation-descendant-reap-failed',
        'why': 'descendant is still alive',
        'execution': {'kind': 'finished', 'status': 'exited', 'exit_code': 7},
        'cleanup': {'state': 'unconfirmed', 'why': 'descendant is still alive'},
    })
    assert process.status_refusal.code == 'isolation-descendant-reap-failed'
    assert process.status_refusal.detail['execution_status'] == 'exited'
    assert process.status_refusal.detail['exit_code'] == 7
    assert process.status_refusal.detail['cleanup']['state'] == 'unconfirmed'
    assert process.wait(timeout=0) < 0
    process.close(deadline=0)
    assert process.final_status == 'refused'
