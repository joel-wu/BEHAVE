"""No specification leaves a compared output field free.

T13. Field-by-field comparison on every transaction is the contract only while the
specifications determine every output field of every transaction. A problem that says an
output field is don't-care makes a correct design, driving anything on that field, report
as a mismatch -- the wrong answer in the worst direction, since it is the artifact that
looks at fault.

A check rather than a mechanism, deliberately. A validity-aware comparison would be code
no problem exercises, and `lowering.py` records what that costs: an unused rule believed
the moment someone calls it. When this fires on a real problem, the mechanism gets built
with a case to test it against.

**Two things this had to get right, and measuring is what said so.**

*What counts as an output.* Not the `out_` prefix -- `out_ready` is the environment's
back-pressure, an input. Not the elaborated schema either, which lists `out_valid`, a port
no reference model emits: asking it also costs three and a half minutes, because it runs
the elaborator on all 836 specifications. The compared fields are exactly the keys the
behavior model returns, which is both the cheap question and the right one.

*What counts as leaving one free.* Eleven specifications mention don't-care wording beside
an output name. Six are about an output payload:

    round_robin_arbiter      `out_data` ... is don't-care unless `out_valid = 1`
    mamba_conv1d_depthwise   When `out_valid = 0`, `out_y` is don't-care
    zamba2_hybrid_layer      the output payload value when `out_valid == 0` is don't-care
    jpeg_idct_2d_8x8_int     values on `out_pix_0` through `out_pix_63` are don't-care
    sha256_compress_round    values on `out_h0`..`out_h7` are don't-care when `out_valid` is low
    siphash_round            may drive any values on `out_v0`, ...

which reads at first like six problems already doing what T13 warns about. They are not,
and the reason is that **every one of them scopes the don't-care with the validity signal,
and no behavior model emits that signal**: the six models return their payload fields
only. At the transaction level the model emits one transaction per accepted input with
every payload field determined, and an RTL history is reconstructed from signal transfers
-- so the comparison never reads a payload on a cycle carrying no transaction, which is
the only place these specifications leave it free.

So what breaks the contract is a don't-care on a compared field that is **not** so scoped,
and that is the gate. The other five of the eleven are don't-cares the specification then
*resolves* to a value, or ones whose subject is a Karnaugh-map input cell or an input port
rather than an output.
"""
import ast
import re

import pytest

from behave.core.paths import problems_root

#: Wording that says a value is not determined. Read off the population rather than
#: guessed: `ignored` alone appears in 102 specifications and is almost always about an
#: input being accepted and dropped, so it is not here.
DONT_CARE = re.compile(
    r"don'?t[- ]care|do(es)? not care|unspecified|any value|undefined"
    r"|not specified|irrelevant|may be anything",
    re.IGNORECASE)

#: What makes a don't-care harmless: it is scoped to cycles carrying no transaction, and
#: the transaction-level comparison does not read those.
GUARD = re.compile(
    r"valid|no output transaction|not being presented|until an output transaction"
    r"|no transaction|in reset|after reset|while in reset|back-pressure|handshake",
    re.IGNORECASE)

#: And what makes it not a don't-care about an output at all. Both are claims about the
#: subject of the phrase rather than patterns that happened to clear the population.
#:
#: *Resolved*: "resolves don't-care cells to match the minimized expression", "with all
#: don't-care entries chosen as 0", and the reviewed tasks' "explicitly defines it as
#: zero then" or "explicitly completes the original unspecified cells". A specification
#: that fixes the value has specified it; the word is describing the input problem, not
#: the obligation.
#:
#: *Not an output*: a Karnaugh-map cell or entry is an input combination, "`in_y`
#: encodes any value `v`" quantifies the input while the same sentence gives `out_z`
#: exactly, and casez "pattern don't-cares" or "irrelevant inputs" are input bits.
NOT_A_FREEDOM = re.compile(
    r"resolve[sd]?\s+.{0,40}?don'?t[- ]care"
    r"|don'?t[- ]care.{0,40}?(chosen|resolved|treated|fixed)\s+as"
    r"|don'?t[- ]care\s+(cell|entr|input|combination)"
    r"|\bin_[A-Za-z0-9_]+`?\s+(encodes|is|carries|holds)\s+any value"
    r"|karnaugh"
    r"|\bexplicitly\s+(defin|complet)\w*"
    r"|\bpattern\s+don'?t[- ]cares?|\birrelevant\s+inputs?",
    re.IGNORECASE)


def _compared_outputs(behavior_path):
    """The output field names a model actually returns, read off its syntax.

    These are the fields the comparison reads. A port the schema declares and the model
    never emits -- `out_valid` -- is not one of them, which is the distinction the whole
    check turns on.
    """
    names = set()
    try:
        tree = ast.parse(behavior_path.read_text(errors="replace"))
    except SyntaxError:
        return names
    for node in ast.walk(tree):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        mappings = []
        if isinstance(node.value, ast.Dict):
            mappings = [node.value]
        elif isinstance(node.value, (ast.List, ast.Tuple)):
            mappings = [e for e in node.value.elts if isinstance(e, ast.Dict)]
        elif isinstance(node.value, ast.IfExp):
            for arm in (node.value.body, node.value.orelse):
                if isinstance(arm, ast.Dict):
                    mappings.append(arm)
                elif isinstance(arm, (ast.List, ast.Tuple)):
                    mappings += [e for e in arm.elts if isinstance(e, ast.Dict)]
        for mapping in mappings:
            for key in mapping.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    names.add(key.value)
    return names


def _problems():
    return sorted(problems_root().rglob("*/spec.md"))


def _lines_leaving_a_field_free(spec, *, guarded=False):
    """Lines where a don't-care is predicated of a compared field.

    ``guarded`` selects the ones scoped to a non-transaction cycle instead, which is
    what the second test needs to know are still being seen.
    """
    text = spec.read_text(errors="replace")
    outputs = _compared_outputs(spec.parent / "behavior.py")
    if not outputs:
        return []
    named = re.compile(r"\b(" + "|".join(re.escape(n) for n in outputs) + r")\b")
    out = []
    for number, line in enumerate(text.splitlines(), 1):
        if not (DONT_CARE.search(line) and named.search(line)):
            continue
        if NOT_A_FREEDOM.search(line):
            continue
        if bool(GUARD.search(line)) != guarded:
            continue
        out.append((number, line.strip()[:160]))
    return out


def test_the_population_is_there_to_check():
    assert len(_problems()) > 800, "this check is about the dataset and needs it"


def test_no_specification_leaves_a_compared_output_field_free():
    """The gate. It passes today, and the point of it is the day it does not."""
    bad = {}
    for spec in _problems():
        hits = _lines_leaving_a_field_free(spec)
        if hits:
            bad[spec.parent.name] = hits
    assert not bad, (
        f"{len(bad)} specifications leave a compared output field free, which makes a "
        f"correct design report as a mismatch: "
        f"{ {k: v[0] for k, v in list(bad.items())[:3]} }")


def test_the_guarded_ones_are_still_seen_so_the_gate_is_not_vacuous():
    """A gate that passes because it matches nothing is not a gate.

    The six payload don't-cares are scoped to cycles with no transaction, so the gate
    above says nothing about them. This asserts they are still being *seen*: if the
    wording moved and they stopped matching, the gate would go on passing for a new
    reason and nobody would know.
    """
    seen = {spec.parent.name for spec in _problems()
            if _lines_leaving_a_field_free(spec, guarded=True)}
    assert len(seen) >= 5, (
        f"only {len(seen)} specifications scope a don't-care on a compared field: "
        f"{sorted(seen)}. Six did when this was written, so either the wording moved "
        f"or the pattern stopped matching it.")


@pytest.mark.parametrize("line", [
    "When `out_valid = 0`, `out_y` is don't-care.",
    "While no output transaction is being presented, `out_y` is don't-care.",
])
def test_a_scoped_dont_care_is_not_a_freedom(line):
    assert DONT_CARE.search(line) and GUARD.search(line)


@pytest.mark.parametrize("line", [
    "This specification resolves don't-care cells to match the minimized expression.",
    "derived from the Karnaugh map with all don't-care entries chosen as `0`.",
    "If `in_y` encodes any value `v`, `out_z = v mod p`.",
    "The source leaves `out_y` unspecified when done=0; this task explicitly defines it as zero.",
    "Input values are binary; pattern don't-cares do not introduce unknown input bits.",
])
def test_a_resolved_or_input_side_dont_care_is_not_one(line):
    assert DONT_CARE.search(line) and NOT_A_FREEDOM.search(line)


def test_an_unguarded_dont_care_would_fail():
    """The shape the gate is for, so it is known to fire on something."""
    line = "The value of `out_y` is don't-care."
    assert DONT_CARE.search(line)
    assert not GUARD.search(line) and not NOT_A_FREEDOM.search(line)
