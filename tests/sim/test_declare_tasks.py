"""The task contract is derived before, and independently of, any realization."""

from .test_wave1_closure import _tool_module


def _options(**changes):
    options = {
        "validate_reference": True,
    }
    options.update(changes)
    return options


def test_failed_reference_validation_does_not_suppress_the_contract(monkeypatch):
    declare = _tool_module("declare_tasks")
    monkeypatch.setattr(
        declare, "declaration_for",
        lambda *_args, **_kwargs: ("task\n", []),
    )
    monkeypatch.setattr(
        declare, "validate_reference",
        lambda *_args, **_kwargs: (False, "concrete-mismatch"),
    )

    _path, task, missing, _elapsed, validation = declare._derive_one(
        ("/does/not/need/to/exist", _options()))

    assert (task, missing) == ("task\n", [])
    assert validation == "concrete-mismatch"


def test_reference_validation_is_optional(monkeypatch):
    declare = _tool_module("declare_tasks")
    monkeypatch.setattr(
        declare, "declaration_for",
        lambda *_args, **_kwargs: ("task\n", []),
    )

    def should_not_run(*_args, **_kwargs):
        raise AssertionError("reference validation was not requested")

    monkeypatch.setattr(declare, "validate_reference", should_not_run)
    _path, task, missing, _elapsed, validation = declare._derive_one(
        ("/does/not/need/to/exist", _options(validate_reference=False)))

    assert (task, missing) == ("task\n", [])
    assert validation is None
