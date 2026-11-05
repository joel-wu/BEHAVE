Edit and test the workspace files across turns. When ready, submit the RTL, Python functional verification model (Behavior), and cocotb testbench together in one standalone tool call. This ends the attempt.

Provide a selection object for each of `rtl`, `behavior`, and `testbench` with these fields:

- `files`: The workspace files needed by this artifact, including its entry file and local dependencies. Use explicit paths relative to the workspace root, such as `rtl.sv`. Include required RTL submodules and include files, Python helper modules, and test data as applicable. Listing an entry file does not automatically collect its workspace dependencies.
- `entry_file`: The file containing the entry point. This path must also appear in `files`.
- `entry_symbol`: The name to use inside the entry file, as described below.

| Artifact | `entry_file` | `entry_symbol` |
| --- | --- | --- |
| `rtl` | Top-level RTL source file | Top-level module name from the task interface |
| `behavior` | Python verification model file | Behavior class name from the task interface |
| `testbench` | Python file containing cocotb tests | One test function name, or `*` to run all tests in that module |

Submitted-TB execution also makes the submitted Behavior files available to the testbench. Include any additional TB helpers and test data in `testbench.files`.

Example of complete `submit` arguments for a task whose RTL top module is `top` and Behavior class is `Behavior`. Here `rtl.sv` contains `top`, which uses submodules defined in `datapath.sv` and `fifo.sv`. All files are at the workspace root, so no subdirectories are needed:

```json
{
  "rtl": {
    "files": ["rtl.sv", "datapath.sv", "fifo.sv"],
    "entry_file": "rtl.sv",
    "entry_symbol": "top"
  },
  "behavior": {
    "files": ["behavior.py"],
    "entry_file": "behavior.py",
    "entry_symbol": "Behavior"
  },
  "testbench": {
    "files": ["test_design.py"],
    "entry_file": "test_design.py",
    "entry_symbol": "*"
  }
}
```

Use your actual file paths and the entry names required by the task. The example does not prescribe filenames. The tool captures the current contents of the selected files and returns a submission record. It does not run tests or assign a correctness score.
