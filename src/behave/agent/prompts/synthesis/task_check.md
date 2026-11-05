# Behavior execution check

Use the existing Bash tool to check the current Behavior and interface:

```bash
python /tools/behave_sim_check.py check.json
```

Write `check.json` with your own Behavior file selection:

```json
{
  "spec": "spec.md",
  "behavior": {
    "files": ["behavior.py"],
    "entry_file": "behavior.py",
    "entry_symbol": "Model"
  },
  "contract": "contract.json",
  "run_policy": "run_policy.json"
}
```

Use your actual file names and entry symbol. `run_policy` may be omitted to use
runtime generation settings; for Builder draft submission it remains a required file
path whose JSON content can be null. Test counts, sequence lengths and execution
budgets come from the supplied runtime, not additional command-line settings.

Include the public `spec` path. The command first checks its RTL declaration
against the contract (port names, widths, signedness and control bindings).
This elaborates only the declaration; you do not need an RTL implementation.
An interface failure is reported before Behavior execution. A blind implementer
checks the supplied public spec and their own model, without access to B1.

The command uses the real Sim execution and comparison path with your submitted
Behavior on both sides. This checks execution under the declared interface,
not functional correctness. It does not replace independent B1/B2 comparison
or establish exact lowering/bounded-analysis support. Only the
files you select are sent to the check; there is no hidden reference.

The reply names a `report.md` and linked public diagnostics under `sim_check/`.
Read the report and relevant inputs/observations, fix your implementation or
interface, and rerun the command as needed. A match exits 0, a mismatch exits 1,
and an unavailable result or invalid request exits 2. Unavailable is not a pass.
Results remain in the workspace after the command exits.

`.construction/behavior_environment.json` describes this installed Sim version's
Behavior names and capabilities. Reuse `/docs/behavior.md` and numerical guides;
name availability alone does not guarantee support for every program.
