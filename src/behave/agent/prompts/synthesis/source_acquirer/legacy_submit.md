Submit materials and experience together to finish this attempt. Each material
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
The materials list may be empty. In experience, construction and solver are the complete updated texts
of the two persistent views, not file paths or incremental changes. Keep useful
prior notes unchanged when there is no supported update. A view may be empty if
there are no useful prior or new lessons. Submission preserves these
notes and hands sources to construction; it does not approve or publish tasks.
