Compare the selected RTL with the configured Behavior reference using BEHAVE-Sim.
Select source files, entry file and entry symbol as for submission. The service
generates stimuli, drives the interface and compares outputs under the task's
protocol requirements. This is a development check, not final submission.

${reference}

`matched` is true or false when comparison completes, and null when no comparison
decision is available. Final evaluation is performed separately.

The runtime allows up to ${timeout_seconds} seconds for one comparison, including
preparation and execution. This is a timeout, not a required duration.
An unavailable result is not a mismatch.
