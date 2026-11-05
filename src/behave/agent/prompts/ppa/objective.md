${search_objective}

Preserve the complete task semantics, including numerical corner cases, reset
and protocol behavior. Do not specialize the implementation to the listed
workload inputs. Establish correctness using the development workflow above
before optimizing area, and recheck the retained versions after source changes.

${measurement_instructions}

An implementation that fails correctness or clock timing is not feasible.
Retain intermediate candidates and
their measurements rather than overwriting their history.

Within the available turns, investigate distinct
resource-sharing, parallelism, buffering or pipelining choices permitted by the
task. Revisions should respond to actual measurements. Measure the versions
you intend to retain. Workload time is measured design time at the fixed clock,
not the simulator's wall-clock execution time.

Finish with finish_search. The evaluator retains all registered candidates,
including failures. Final validation is run separately without further
feedback. No testbench submission is required; this does not remove the
development workflow's testing duties. The reported power is a vectorless
estimate, not a workload-energy measurement or optimization objective.
