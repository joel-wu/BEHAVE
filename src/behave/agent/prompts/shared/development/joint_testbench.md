Jointly develop three artifacts for the supplied hardware task: a synthesizable RTL implementation, a Python functional verification model (the behavior model), and a cocotb testbench that checks the RTL against that model. Read the specification before implementing. Follow the task's numerical, state, reset, protocol, and timing requirements. The specification and supplied public files are read-only reference inputs.

${behavior_model}

Write a cocotb testbench that drives the RTL, calls the verification model with the task-defined inputs, and asserts that the RTL observations match its returned outputs. Also check the task's protocol, reset, and timing requirements. Use the provided development tools to run tests and investigate mismatches. Iteratively refine the RTL, verification model, and testbench using test feedback. Resolve disagreements against the specification and correct the relevant artifacts.
