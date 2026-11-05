Submit the complete compact replacements in experience.construction and
experience.solver. Their combined UTF-8 size must not exceed the supplied budget.
These are text values, not paths. This atomically publishes the two views for
this batch; it does not change task proposals, rewards or construction approval.
The experience argument is an object, not a JSON-encoded string:
{"experience": {"construction": "Concise construction lessons.", "solver": "Concise solver observations."}}
