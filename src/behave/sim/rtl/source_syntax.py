"""Traversal of slang's concrete syntax, never token text interpreted as HDL."""

# Slang uses LoopStatement for while/repeat, distinguished by keyword.
LOOP_STATEMENTS = {"ForLoopStatement", "ForeachLoopStatement", "LoopStatement",
                   "DoWhileStatement", "ForeverStatement"}


def concrete_token(node):
    """Exact unexpanded token spelling; namespace policy belongs to the caller."""
    trivia = node.get("trivia", [])
    allowed = {"Whitespace", "EndOfLine", "LineComment", "BlockComment"}
    if type(node.get("text")) is not str or type(trivia) is not list:
        raise ValueError("source observation requires unexpanded, concrete tokens and trivia")
    pieces = []
    for item in trivia:
        if type(item) is dict and item.get("kind") == "Directive" \
                and type(item.get("syntax")) is dict:
            # Slang stores e.g. `timescale as concrete syntax inside trivia.
            # Preserve those bytes; MacroUsage / SkippedTokens still fail below.
            pieces.extend("".join(concrete_token(token)) for token in walk(item["syntax"])
                          if type(token) is dict and "text" in token)
        elif type(item) is dict and item.get("kind") in allowed \
                and type(item.get("text")) is str:
            pieces.append(item["text"])
        else:
            raise ValueError("source observation requires unexpanded, concrete tokens and trivia")
    return "".join(pieces), node["text"]


def children(node):
    if type(node) is list:
        return tuple(node)
    if type(node) is not dict or "text" in node:
        return ()
    return tuple(value for key, value in node.items()
                 if key != "kind" and type(value) in (dict, list))


def walk(node):
    yield node
    for child in children(node):
        yield from walk(child)
