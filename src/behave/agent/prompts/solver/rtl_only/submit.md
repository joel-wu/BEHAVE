When ready, submit the completed RTL in one standalone tool call. This ends the attempt.

Provide an `rtl` selection object with these fields:

- `files`: The workspace files needed by the RTL, including the entry file, submodules, and include files. Use explicit paths relative to the workspace root. Listing an entry file does not automatically collect its workspace dependencies.
- `entry_file`: The top-level RTL source file. This path must also appear in `files`.
- `entry_symbol`: The top-level module name required by the task interface.

Example for a task whose top module is `top`, defined in `rtl.sv` and using a submodule defined in `datapath.sv`:

```json
{
  "rtl": {
    "files": ["rtl.sv", "datapath.sv"],
    "entry_file": "rtl.sv",
    "entry_symbol": "top"
  }
}
```

Use your actual file paths and the entry name required by the task. The example does not prescribe filenames. The tool captures the current contents of the selected files and returns a submission record.
