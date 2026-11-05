# Scoring-policy fixture

This is a test-owned one-entry ready/valid register, **not** the benchmark's
dual-edge flip-flop. Every accepted input bit produces one identical output bit.
An output remains stable under backpressure; consumption and replacement can
occur at the same clock edge. Reset clears the pending output and stored bit.

The legacy policy regressions used the old handshake-wrapped `dualedge` task
for this role. The dataset now correctly preserves its native edge-sensitive
interface. These tests need a fixed handshake task, not whichever implementation
happens to occupy that dataset path. Spec, reference, RTL and both declarations
are therefore owned together here. No production task is restored or rewritten.

`drop_fourth.sv.txt` is a deliberate negative candidate with the same interface:
it accepts four inputs, answers the first three, and keeps the fourth pending
forever. It checks that missing output is a concrete mismatch rather than
"no result". It is never used as the reference. The `.txt` suffix keeps it out
of ordinary HDL source discovery; the negative test installs it as `rtl.sv` in
its own copy.

`stage_handshake_register()` creates a fresh copy per caller. Changes to one
copy cannot affect another test or the source fixture. Run configuration is
explicit and unchanged: reset hold 3, drain 64, stall every 3, horizon 256,
queue bound 64. It is a regression configuration, not the public default.
