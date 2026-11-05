"""RTL frontend components.

The compiler-owned source image (``source_ir``, ``source_task``) is the one exact
relation: local Search windows, protocol obligations and bounded Analysis all
consume it through the isolated worker.  An independent ``RTLArtifact``/Verilator
replay audits every published SAT witness; a provider without that capability is
refused at binding.  Yosys remains the RTLIL site, state-ownership and clock-data
reader behind the native observer, not a second relation.
"""
