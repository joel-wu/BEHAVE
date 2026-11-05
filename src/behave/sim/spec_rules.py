"""Compatibility rule for version-5 manifests derived from specification prose.

Current manifests are derived from the trusted Behavior boundary by
``reference_rules``.  This module remains executable so the runtime can rederive and
validate existing version-5 files instead of silently reinterpreting them.

**The rule, and why it is shaped this way.** The first version wrote
`answers_per_transaction = 1` wherever the phrase "exactly one output transaction"
appeared. In `numeric_pool_dense_linalg_blas_ddot` it appears in "On accepted input
transaction 4 of a block, ... emits exactly one output transaction" -- a statement about
the fifth input of every five -- and the file said every input owes one. So a sentence
must quantify over every accepted input and not be conditioned on which one, and the
specification must carry no signal anywhere of a non-uniform ratio: a block, a frame, a
warm-up, an input that produces no output. Both halves are conservative in the same
direction, and a task this cannot read stays undeclared.
"""

from __future__ import annotations

import hashlib
import re

__all__ = ["GENERATOR", "KNOWN_GENERATORS", "derive", "spec_digest"]

#: Bumped whenever the rules below change what would be derived. A manifest naming
#: another was written by a rule this build has never seen, and the fields in it may mean
#: something else. Version 5 retains the complete specification digest in the
#: generated provenance; version 4 used only a display-length prefix.
GENERATOR = 5

#: The generators whose output this build can re-derive and check. Older manifests are
#: refused rather than read hopefully: provenance written with a different rule or
#: identity width is not silently reinterpreted by this build.
KNOWN_GENERATORS = (5,)

#: A channel carries transactions rather than a continuously meaningful sampled value.
#: Both endpoints are declared separately, so both sentences are required.
_INPUT_IS_TRANSACTIONAL = re.compile(
    r"[^.\n]*\baccepted input transaction\b[^.\n]*\.", re.IGNORECASE)
_OUTPUT_IS_TRANSACTIONAL = re.compile(
    r"[^.\n]*\boutput transaction\b[^.\n]*\.", re.IGNORECASE)

#: The sentences that might be stating the ratio. Finding one is not deriving from it:
#: what it says is decided by `_CANONICAL_RATIO` below.
_MENTIONS_THE_RATIO = re.compile(
    r"[^.\n]*\bexactly one output transaction\b[^.\n]*\.", re.IGNORECASE)

#: The **whole** sentence forms this build will read a 1:1 ratio out of.
#:
#: The previous rule was `each|every|one|per ... accepted input ... exactly one output
#: transaction` with anything allowed in between, and it read a ratio out of sentences
#: that state the opposite. Both of these came back `answers_per_transaction = 1`:
#:
#:     Per four accepted input transactions, the design emits exactly one output
#:     transaction.
#:     One in every four accepted input transactions produces exactly one output
#:     transaction.
#:
#: A quantifier is not a universal quantifier because it is spelled `one` or `per`. So
#: the match is anchored at the start of the sentence and the whole of the subject has
#: to be one of a small set of forms: the sentence must *begin* by quantifying over
#: accepted inputs and go straight to the verb, with at most a named subject between
#: them. `Per four accepted...` does not begin that way; `One in every four accepted...`
#: begins with `One` and then says `in every four`, which is not `one accepted input`.
#:
#: This is deliberately narrow, and it costs coverage: 284 of the 343 specifications
#: that the loose rule read a ratio out of match a canonical form. The other 59 are each
#: a different sentence with something in the middle -- "each accepted input transaction
#: **represents exactly one tuple and** produces exactly one output transaction", "after
#: reset is released, each accepted..." -- and every one of them is a sentence a human
#: would have to read to be sure. They stay undeclared, which is the direction this file
#: has had to be pushed twice already.
_CANONICAL_RATIO = (
    # `[For] each|every|one accepted input [transaction][,] [the module] produces
    # exactly one output transaction`
    re.compile(
        r"^(?:for\s+)?(?:each|every|one)\s+accepted\s+input(?:\s+transaction)?\s*,?\s+"
        r"(?:the\s+\w+(?:\s+\w+)?\s+)?(?:always\s+)?"
        r"(?:produces|emits|yields|generates|returns)\s+exactly\s+one\s+"
        r"output\s+transaction\b", re.IGNORECASE),
    # The passive of the same statement.
    re.compile(
        r"^for\s+(?:each|every)\s+accepted\s+input(?:\s+transaction)?\s*,\s*"
        r"exactly\s+one\s+output\s+transaction\s+is\s+"
        r"(?:produced|emitted|returned)\b", re.IGNORECASE),
)


def _states_one_answer(sentence):
    """Whether this sentence states 1:1 in a form this build reads without judgement.

    A leading clause separated by a colon is allowed and dropped -- "The module is 1:1:
    every accepted input transaction produces exactly one output transaction" is the
    canonical sentence with a label in front of it -- and so is a list bullet. Nothing
    else is: what remains has to match a canonical form from its first word.
    """
    tail = sentence.split(":")[-1] if ":" in sentence else sentence
    tail = tail.lstrip("-* ").strip()
    return any(pattern.match(tail) for pattern in _CANONICAL_RATIO)

#: What disqualifies such a sentence even when it matches: it is about a particular input
#: rather than about every one.
_CONDITIONED = re.compile(
    r"\bon accepted input transaction\s+\d|\bof (?:a|the|each|every) block\b|"
    r"\bwhen\b|\bif\b|\bafter the\b|\bfinal\b|\blast\b|\bfifth\b|\bfourth\b", re.IGNORECASE)

#: What disqualifies the whole task, wherever it appears: the specification is describing
#: a ratio this build's schema cannot state. Left undeclared rather than flattened to 1:1.
_NON_UNIFORM = (
    "returns `none`", "returns none on", "produces no output", "emits no output",
    "no output transaction", "does not produce an output", "does not emit",
    "block of", "blocks of", "per block", "of a block", "of the block",
    "warm-up", "warmup", "frame", "burst",
    "every second accepted", "every third accepted",
)
# Deliberately **not** here: "accumulat". A design that keeps a running sum and answers
# every input with the sum so far is uniformly 1:1, and `ai_ml_arch_systolic_pe_basic_mac`
# is one -- its specification says "Each accepted input transaction produces exactly one
# output transaction" in as many words. Internal state is not a ratio.


#: An output that is *always* valid is not a stream of transactions: it is a
#: continuously meaningful value, which is what `Kind.SAMPLED` is for and what this
#: build's authoritative path does not compare. Declaring it transactional makes every
#: cycle the environment is ready into a transfer.
#:
#: One task in this population says it, and it was scored: `cvdp_agentic_queue_0001`, a
#: first-word-fall-through FIFO whose specification says "`out_valid` shall be
#: permanently asserted high" and "presents the oldest stored word continuously on the
#: output" -- and which also contains the sentence this rule keys on, "Every accepted
#: input transaction produces exactly one output transaction". So it derived a
#: transactional output, the design produced 20 outputs the reference did not expect, and
#: 167 mismatches came back as a design that computes the wrong answer. It computes the
#: right answer; the declaration was wrong.
#:
#: Deliberately about the **output**. `in_ready` is permanently asserted in sixteen more
#: specifications and that is ordinary -- it says the design never back-pressures its
#: input, not that the input is not transactional. Matching those would have withdrawn
#: sixteen manifests for a sentence that means something else, which is the mistake this
#: comment exists to stop me repeating: I counted six before reading them.
_OUTPUT_ALWAYS_VALID = (
    re.compile(r"[^.\n]*\bout_valid\b[^.\n]*\b(permanently|always|at all times|"
               r"continuously)\b[^.\n]*\.", re.IGNORECASE),
    re.compile(r"[^.\n]*\b(continuously on the output|presents?[^.\n]{0,40}"
               r"continuously)\b[^.\n]*\.", re.IGNORECASE),
    # **The same shape, said the other way round.** `cvdp_copilot_events_to_apb_0001`
    # says "`in_ready` is 1 and `out_valid` is 1 whenever not held in reset" -- an
    # assertion with no transaction behind it -- and produced 72 outputs the reference
    # never expected. The first two patterns did not see it.
    #
    # What separates this from the forty other specifications that mention `out_valid`
    # holding is the **qualifier**: theirs are transaction-relative, "whenever an output
    # transaction is pending", "only while presenting", "iff at least one `in_valid`".
    # Those are ordinary handshake statements and matching them would withdraw fourteen
    # manifests for saying how a handshake works. This one's qualifier is absolute or
    # relative only to reset, which is what makes the output a sampled value.
    #
    # Read before written, this time: the forty sentences were printed and gone through,
    # and this pattern matches exactly one of them.
    re.compile(r"[^.\n]*\bout_valid\b[^.\n]*\b(?:is|=|shall be|remains?|stays?)\s*"
               r"[`'\"]?(?:1|1'b1|high|asserted)[`'\"]?[^.\n]*"
               r"\b(?:whenever not (?:held )?in reset|outside (?:of )?reset|"
               r"at all times|in every cycle|every cycle|unconditionally)\b[^.\n]*\.",
               re.IGNORECASE),
)


def spec_digest(spec):
    """The complete semantic identity recorded beside a generated declaration.

    This value is checked without carrying the specification bytes into every record.
    It therefore cannot be a display abbreviation: equality must mean equality of the
    SHA-256 image used by the manifest authority.
    """
    return hashlib.sha256(spec.encode()).hexdigest()


def _sentence(spec, pattern):
    """The sentence that states this, and the line it is on. `None` where absent."""
    found = pattern.search(spec)
    if not found:
        return None
    line = spec.count("\n", 0, found.start()) + 1
    return " ".join(found.group(0).split()), line


def derive(spec):
    """`(fields, cited, missing)` for one specification.

    ``fields`` is what a manifest generated from this specification must say, as the
    flat keys a reader of `task.toml` sees. ``cited`` maps each to the sentence and line
    it was read from, for the file's comments. ``missing`` is non-empty where this
    specification does not say enough, and then ``fields`` is empty: a partial derivation
    is not a declaration.
    """
    missing, cited, fields = [], {}, {}

    # The disqualifiers before the sentences: a specification that describes blocks or a
    # warm-up is not one where a single well-formed sentence settles the question.
    found = [word for word in _NON_UNIFORM if word in spec.lower()]
    if found:
        missing.append(f"non-uniform-ratio: {found[0]}")

    for pattern in _OUTPUT_ALWAYS_VALID:
        said = pattern.search(spec)
        if said:
            missing.append(
                "output-is-continuously-valid: "
                + " ".join(said.group(0).split())[:80])
            break

    for pattern, field, key in (
            (_INPUT_IS_TRANSACTIONAL, "endpoint.in.kind", "in"),
            (_OUTPUT_IS_TRANSACTIONAL, "endpoint.out.kind", "out"),
            (_MENTIONS_THE_RATIO, "endpoint.out.answers_per_transaction", "ratio")):
        got = _sentence(spec, pattern)
        if got is None:
            missing.append(field)
        elif key == "ratio" and _CONDITIONED.search(got[0]):
            # The blas_ddot shape: a sentence with the right words about one particular
            # input. Named separately, because "the specification does not say" and "the
            # specification says something narrower" are different things to fix.
            missing.append(f"{field}: stated for one input, not for every one")
        elif key == "ratio" and not _states_one_answer(got[0]):
            # A sentence that mentions the ratio in a form this build does not read
            # without judgement -- `Per four accepted input transactions, ... exactly one
            # output transaction` is the case that made this necessary.
            missing.append(f"{field}: not stated in a form this build reads "
                           f"({got[0][:60]})")
        else:
            cited[key] = got
    if missing:
        return {}, {}, missing

    fields["endpoint.in.kind"] = "transaction"
    fields["endpoint.out.kind"] = "transaction"
    fields["endpoint.out.answers_per_transaction"] = 1
    return fields, cited, []


def fields_of(endpoints):
    """A read manifest's semantic fields, in the shape `derive` returns.

    The comparison is between two dictionaries of the same shape, so the mapping from
    what was parsed to what was derived lives in one place rather than at the comparison.
    """
    out = {}
    for name, block in dict(endpoints or {}).items():
        for key, value in dict(block).items():
            out[f"endpoint.{name}.{key}"] = getattr(value, "value", value)
    return out
