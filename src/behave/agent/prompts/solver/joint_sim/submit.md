Submit the final RTL and Python verification model together. This ends the attempt. Use task-workspace-relative source paths. `files` lists each artifact's entry file and source dependencies. `entry_symbol` is the RTL top module or Python model class required by the specification.

Example:

```json
{
  "rtl": {
    "files": ["rtl/top.sv", "rtl/arithmetic.sv"],
    "entry_file": "rtl/top.sv",
    "entry_symbol": "top"
  },
  "behavior": {
    "files": ["model/behavior.py", "model/arithmetic.py"],
    "entry_file": "model/behavior.py",
    "entry_symbol": "Model"
  }
}
```

Use your actual file names and the task's required entry symbols. No testbench is requested.
