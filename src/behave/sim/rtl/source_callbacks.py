"""Source scope identity is explicit, not inferred from a DPI import's scope."""

SCOPE_TOKEN = "__behave_native_event_scope_token"


def callback_declaration(callback: str, *, scoped: bool, owns_scope: bool = True) -> str:
    # %m is evaluated in the original module / generate scope, once during
    # construction. The immutable collector inventory resolves it to an integer;
    # callbacks and copied helper arguments need no strings or VPI signal reads.
    if not owns_scope:
        # Compilation-unit helpers have no physical instance. Their caller
        # supplies the original instance token through the existing helper ABI;
        # do not register a fictitious $unit scope or infer it inside DPI.
        if scoped:
            return f'''
import "DPI-C" function void __behave_native_event_source_hit(input int unsigned id, scope);
function automatic void {callback}(input int unsigned id, scope);
  __behave_native_event_source_hit(id, scope);
endfunction
'''
        return f'''
import "DPI-C" {callback} = function void __behave_native_flat_hit(input int unsigned id);
function automatic void {callback}(input int unsigned id, scope);
  __behave_native_flat_hit(id);
endfunction
'''
    if scoped:
        return f"""
import "DPI-C" function int unsigned __behave_native_event_scope_id(input string name);
import "DPI-C" function void __behave_native_event_source_hit(input int unsigned id, scope);
int unsigned {SCOPE_TOKEN} = __behave_native_event_scope_id($sformatf("%m"));
function automatic void {callback}(input int unsigned id, scope = {SCOPE_TOKEN});
  __behave_native_event_source_hit(id, scope);
endfunction
"""
    return f"""
import "DPI-C" {callback} = function void __behave_native_flat_hit(input int unsigned id);
localparam int unsigned {SCOPE_TOKEN} = 0;
function automatic void {callback}(input int unsigned id, scope = {SCOPE_TOKEN});
  __behave_native_flat_hit(id);
endfunction
"""
