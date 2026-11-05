"""Public export lists resolve both direct and lazy names."""

import importlib

import pytest


@pytest.mark.parametrize('name', [
    'behave.sim.rtl.analysis',
    'behave.sim.symbolic.plan',
])
def test_declared_exports_exist(name):
    module = importlib.import_module(name)
    for exported in module.__all__:
        assert getattr(module, exported) is not None
