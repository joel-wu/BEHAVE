"""Shared finite values, compiler IR, planning and symbolic construction.

Value/IR modules do not load source frontends, workers or solvers. Formula
encoding and transition templates load the solver only when imported explicitly.
Language lowering, execution sessions and goal classification remain consumers.
"""
