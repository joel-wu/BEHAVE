"""Lower helper early exits in the shared compiler input, not native source.

Assignment to the declared function result retains its SV conversion. A private
per-invocation flag suppresses only statements following an executed return;
conditions, case selectors and return expressions are never evaluated twice.
Token-sized edits leave declarations and expression/source coordinates intact.

This pass handles sequential / conditional / case control. A return inside a
loop or suspending control is left to the compiler, which still refuses it: a
guard on the loop body alone would incorrectly execute the loop's later steps.
"""

from .constant_arrays import concrete_spans
from .source_replacements import Replacement, SourceReplacements
from .source_syntax import children, walk

VERSION = "rtl-helper-returns/1"
_HELPERS = {"FunctionDeclaration", "TaskDeclaration"}
_BRANCHES = {"ConditionalStatement", "CaseStatement"}


def normalize_helper_returns(source, cst):
    helpers = [node for tree in cst["syntaxTrees"] for node in walk(tree["root"])
               if type(node) is dict and node.get("kind") in _HELPERS
               and any(type(child) is dict and child.get("kind") == "ReturnStatement"
                       for child in walk(node["items"]))]
    if not helpers:
        return SourceReplacements(source, ())
    root, spans = concrete_spans(source, cst)
    occupied = {node["text"].lstrip("\\") for node in walk(root)
                if type(node) is dict and node.get("kind") == "Identifier"}
    edits = {}

    def token_edit(token, *, before="", after="", text=None):
        start, end = spans[id(token)]
        old = edits.get(start, Replacement(start, end, token["text"]))
        edits[start] = Replacement(start, end, before + (old.text if text is None else text) + after)

    def surround(node, before, after):
        tokens = [item for item in walk(node) if type(item) is dict and "text" in item]
        token_edit(tokens[0], before=before)
        token_edit(tokens[-1], after=after)

    def has_return(node):
        return any(type(item) is dict and item.get("kind") == "ReturnStatement" for item in walk(node))

    def statements(node):
        # Branch wrappers (else/case items) are not themselves statements.
        for child in children(node):
            yield from branch_statements(child)

    def branch_statements(node):
        if type(node) is list:
            for item in node:
                yield from branch_statements(item)
        elif type(node) is dict:
            if node.get("kind", "").endswith("Statement"):
                yield node
            else:
                yield from statements(node)

    def supported(node):
        if not has_return(node) or node.get("kind") == "ReturnStatement":
            return True
        if node.get("kind") == "SequentialBlockStatement":
            return all(supported(item) for item in node["items"])
        return node.get("kind") in _BRANCHES and all(supported(item) for item in statements(node))

    for helper in helpers:
        if not all(supported(item) for item in helper["items"]):
            continue
        name = helper["prototype"]["name"]
        if name.get("kind") != "IdentifierName":
            continue
        # ``return expr`` targets the implicit result even if an inner scope
        # declares a variable with that spelling. A plain assignment would
        # instead write that local. Leave it to the compiler, never misbind it.
        spelling = name["identifier"]["text"].lstrip("\\")
        if any(type(node) is dict and node.get("kind") == "Declarator"
               and node["name"]["text"].lstrip("\\") == spelling
               for node in walk(helper["items"])):
            continue
        result = name["identifier"]["text"] + " "
        items = helper["items"]
        last = items[-1]
        if last.get("kind") == "ReturnStatement" and not any(has_return(item) for item in items[:-1]):
            # Falling off the helper already exits. Do not build a flag or
            # guarded control network for its sole final return.
            token_edit(last["returnKeyword"], text=result + "=" if "returnValue" in last else "")
            continue
        index = 0
        while (flag := f"__behave_return_{index}") in occupied:
            index += 1
        occupied.add(flag)

        def rewrite(node, result=result):
            kind = node.get("kind")
            if kind == "ReturnStatement":
                token_edit(node["returnKeyword"], text="begin " +
                           (result + "=" if "returnValue" in node else ""))
                token_edit(node["semi"], after=f" {flag}=1'b1; end")
            elif kind == "SequentialBlockStatement":
                sequence(node["items"])
            elif kind in _BRANCHES:
                for statement in statements(node):
                    rewrite(statement)

        def sequence(items):
            may_exit = False
            for item in items:
                rewrite(item)
                if may_exit:
                    surround(item, f"if (!{flag}) begin ", " end")
                may_exit |= has_return(item)

        sequence(helper["items"])
        first = next(item for item in helper["items"] if item["kind"].endswith("Statement")
                     and item["kind"] not in {"ParameterDeclarationStatement", "VariableDeclarationStatement"})
        surround(first, f"reg {flag}; begin {flag}=1'b0; ", "")
        token_edit(helper["end"], before="end ")
    return SourceReplacements(source, tuple(edits[key] for key in sorted(edits)))
