Submit the final RTL. This ends the attempt. Use task-workspace-relative paths. `files` lists the entry file and its source dependencies. `entry_symbol` is the top module required by the specification.

Example:

```json
{
  "rtl": {
    "files": ["rtl/top.sv", "rtl/arithmetic.sv"],
    "entry_file": "rtl/top.sv",
    "entry_symbol": "top"
  }
}
```

Use your actual file names and the task's required top module. No Python model or testbench is requested.
