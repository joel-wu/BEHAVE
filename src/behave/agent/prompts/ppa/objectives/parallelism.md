Explore the area-time tradeoff starting with approximately ${parallelism_lanes}
parallel element-processing lanes. A lane is a reusable compute datapath, not a
separate input or output port. Share computation across elements where useful.
Choose your own state storage, scheduling, buffering and pipeline boundaries.

Seek low mapped cell area and short workload completion time while preserving
correctness and meeting the fixed clock's timing constraints. Use the fixed
workload and cell library in the experiment record. This lane count guides
exploration; it is not a correctness requirement or an assigned cycle count.
There is no separate workload completion-time target. Retain useful measured
tradeoffs rather than adding idle cycles to make a design slower.
