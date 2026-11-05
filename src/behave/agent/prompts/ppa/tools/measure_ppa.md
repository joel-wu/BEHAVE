Measure a frozen RTL candidate. The configured EDA flow provides area and timing;
BEHAVE-Sim checks the declared workload against the selected Behavior and
measures its completion cycles on the RTL. Completion time is cycles times
the fixed clock period.

Returns area, timing, auxiliary power, workload completion time and comparison
feedback, with `reference_source` identifying the development reference.
Agreement with an agent-authored Behavior is not a golden correctness result.
EDA and comparison reports are saved in the workspace. Use
`run_command` to read the returned report and full EDA log paths.
Timing feedback includes the worst reported paths, their largest delay
increments and available fanout/load measurements.

A failed or timed-out measurement does not end the search. Inspect the reported
reason, revise the design and capture a new candidate. Tool wall time is distinct
from the RTL workload's completion time.
