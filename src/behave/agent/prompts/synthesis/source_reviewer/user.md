Read review/guidance.md, review/scope.json, source/ and draft/.
Also inspect review/rtl_only_spec.md for completeness and model-only text left
outside the removable Behavior interface.
When present, review/adaptations.json records the declared adaptations and
review/construction_brief.json describes the acquisition objective.

For B1/B2 review, use review/implementations.json to locate the two implementations
and read review/comparison/report.md with its linked input/observation diagnostics.
If B2 could not be written, review/reconstruction.json contains its findings
instead. These inputs remain read-only and available throughout this attempt.

Use review/sim_check.md for the Bash Sim entry and review/sim_check.json for a
ready-to-run request. With B2 present it compares B2 against B1. Without B2 it
checks B1 execution only, not correctness. Copy the request into scratch to
select other workspace implementations or replay explicit input sequences.
When available, review/replay_stimuli.json provides the recorded replay input
format even if the comparison passed and has no failure attachments.

Resolve findings against the source, declared scope and public specification
before submitting. Keep genuinely unresolved issues explicit.
