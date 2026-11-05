Measure mapped cell area, timing and auxiliary power for a frozen RTL candidate
using the configured EDA flow. Returns measurements and workspace paths to the EDA report
and full log. Timing feedback includes the worst reported paths, their largest
delay increments and available fanout/load measurements. Read the files with
run_command as needed.

This tool does not run simulation or check correctness, and does not return
workload completion time. A successful EDA result is not a correctness result.

A failed or timed-out measurement does not end the search. Inspect the reported
reason, revise the design and capture a new candidate. Tool wall time is distinct
from circuit timing.
