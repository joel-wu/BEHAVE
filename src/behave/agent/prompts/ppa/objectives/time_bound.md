Minimize mapped cell area subject to functional correctness, the fixed clock's
timing constraints, and a workload completion time of at most ${primary_target_ns} ns.
Use the fixed workload and cell library in the experiment record. This replicate
optimizes this one time bound. Faster completion is allowed, but is not a separate
objective. Resource sharing may use available time slack; there is no need to
delay a result merely to reach the bound. A design exceeding the assigned bound
does not meet this replicate's goal.
