"""Share observer-only context maps without changing source event identities.

Different sites in the same automatic helper often have the same context-to-ID
offsets. Emit that finite map once, with the site's base ID as a value argument,
instead of duplicating a side-effecting callback switch at every source site.
Nonconstant loop-index expressions keep their original context dispatch.
"""

from .source_edits import Insertion


class ContextDispatch:
    def __init__(self):
        self._maps = {}
        self.declarations = []

    def select(self, alternatives, *, definition_start, context, runtime_only):
        """An exact constant-ID selector, or None for nonconstant expressions."""
        if len(alternatives) < 2 or any(not value.isdecimal() for _, value in alternatives):
            return None
        values = [(caller, int(value)) for caller, value in alternatives]
        base = min(value for _, value in values)
        offsets = tuple(sorted((caller, value - base) for caller, value in values))
        key = (definition_start, offsets)
        name = self._maps.get(key)
        if name is None:
            name = f"__behave_native_event_dispatch_{len(self._maps)}"
            self._maps[key] = name
            # Both arguments and the result are observer IDs. Original DUT
            # values, their widths, and their evaluation never cross this call.
            rows = "\n".join(f"    {caller}: {name} = base + 32'd{offset};"
                             for caller, offset in offsets)
            hint = "  /* verilator no_inline_task */\n" if runtime_only else ""
            body = f"""
function automatic int unsigned {name}(input int unsigned context_id, base);
{hint}  case (context_id)
{rows}
    default: {name} = 32'hffffffff;
  endcase
endfunction
"""
            self.declarations.append(Insertion(definition_start, body))
        return f"{name}({context}, 32'd{base})"
