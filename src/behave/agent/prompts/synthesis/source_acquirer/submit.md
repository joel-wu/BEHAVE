Submit materials and experience_delta together to finish this attempt. Each material
is one task proposal and contains a distinct proposal_id, a primary source id,
supporting_source_ids (empty if none), rationale, objective and observation_ids
(which may be empty for exploration). Several proposals may share a source when
their objectives differ. Include dependencies and documents that Builder needs
in supporting_source_ids; all source IDs must come from the permitted library.
`id` is the existing source identifier copied from `library/catalog.jsonl`.
`proposal_id` is your own distinct label for the new task, not a source lookup
key. For example, if the catalog contains `id="source-demo"`, a proposal may
use `{"id":"source-demo","proposal_id":"streaming-saturation"}`.
The objective must not require Builder to produce a reference RTL implementation.
The materials list may be empty. experience_delta.construction and
experience_delta.solver contain only new findings or evidence-backed corrections,
not complete memory replacements or file paths. Identify the scope and supporting
observation references. Do not copy unchanged prior lessons. An empty string means
no new finding, not deletion of old memory. Respect the supplied combined byte
budget. Submission archives these deltas for consolidation and hands proposals
to construction; it does not approve or publish tasks.
