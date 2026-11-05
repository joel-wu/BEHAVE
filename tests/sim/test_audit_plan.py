"""The exact analysis audit plan is typed, closed and versioned."""

import pytest

from behave.sim.core.audit_plan import AuditKind, AuditPlan
from behave.sim.core.refusal import Refused


def test_audit_plan_is_canonical_typed_and_round_trips():
    plan = AuditPlan([AuditKind.STEP, AuditKind.GOAL_SET])

    assert plan.names == ("goal_set_audit", "step_audit")
    assert plan.requires(AuditKind.STEP)
    assert not plan.requires(AuditKind.SOURCE)
    assert AuditPlan.from_dict(plan.as_dict()) == plan
    with pytest.raises(TypeError, match="not a truth value"):
        bool(AuditKind.STEP)
    with pytest.raises(TypeError, match="exact AuditKind"):
        plan.requires("step_audit")


def test_unknown_duplicate_or_different_version_refuses_at_the_wire():
    with pytest.raises(Refused, match="analysis-audit-plan-unknown"):
        AuditPlan.from_names(["new_unimplemented_audit"])
    with pytest.raises(Refused, match="analysis-audit-plan-duplicate"):
        AuditPlan.from_names(["step_audit", "step_audit"])
    with pytest.raises(Refused, match="analysis-audit-plan-version-unsupported"):
        AuditPlan.from_dict({"version": "analysis-audit-plan/0", "audits": []})


def test_callers_cannot_smuggle_strings_or_subclasses_into_a_plan():
    with pytest.raises(TypeError, match="exact AuditKind"):
        AuditPlan(["step_audit"])
    with pytest.raises(TypeError, match="cannot be subclassed"):
        class Forged(AuditPlan):
            pass
