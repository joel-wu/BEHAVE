Assess each declared scope capability exactly once, using its zero-based index,
a supported/unsupported/unclear conclusion, a concise rationale and line citations
from both source/ and draft/. B2 citations may supplement, not replace, that
evidence. Put unresolved issues in findings; use [] when none remain.
Any nonempty findings array prevents approval, even when all assessments say
supported and both feedback strings are empty. Put successful checks, resolved
issues and non-blocking notes in the assessment rationales, not findings.
Calling a finding "informational" or "no action requested" does not remove it.

For reconstruction review, route repairs using the existing fields:

- builder_feedback: required changes to the task, specification, interface or B1.
- implementer_feedback: corrections to B2 under the existing public specification.
- revised_contract: optional workspace path (or null) to a copy of contract.json
  with physical pin bindings corrected to match the unchanged specification.
  This reuses the existing B1 and B2. Automatic declaration and B/B checks must
  pass before the submission completes; otherwise the tool returns a report
  so you can continue in this attempt. Describe the correction in your rationale,
  with empty findings and author feedback when no other issue remains.

Direct contract repair cannot change field types, endpoints, protocols, timing,
input domains, observed outputs or scoring policy. Send those changes to Builder.
Never overwrite the supplied draft files; write the corrected contract elsewhere.

Nonempty findings require at least one actionable feedback field. Naming a
repair inside findings alone does not send it to an author. A submission missing
the recipient will return feedback for you to correct in this same attempt.
If a required source definition is unavailable, describe the gap to Builder
rather than inventing a replacement. Resolved or non-blocking observations
belong in rationales, with empty findings and feedback when no repair is needed.

Use an actual empty JSON string when no repair is requested, not a string
containing quote characters. Feedback must be actionable and
safe for its recipient: do not copy the other author's code or forward the full
B1/B2 diagnostic package. B2 feedback must follow public requirements, not reveal
a hidden answer. If both fields are set, Builder is revised first and B2 is
reconstructed afresh without the old B2 feedback or workspace.

This submission reports your review, not a release decision. It cannot override
a failed, incomplete or stale comparison.
