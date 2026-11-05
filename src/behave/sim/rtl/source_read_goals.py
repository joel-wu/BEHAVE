"""Typed identity functions for declared source-read predicates.

Each lexical point specializes on the original HDL type in each instance. The
extra signed bit carries every unsigned value without changing its magnitude.
Type casts also cover scalar bits and multidimensional packed values, where
indexing the original value with its total bit width is not a valid MSB read.
Only the observer uses that carrier; the DUT receives its unchanged typed value.
"""

from .source_operation_predicates import predicate_expression

READ_GOAL_PACKAGE = "__behave_native_read_goals"


def read_class(name, rules, callback):
    tests = []
    for index, rule in enumerate(rules):
        readable, expression = predicate_expression(rule.bind(), {"value": "sampled"},
            carriers={"value": ("sampled", "$bits(T)+1")})
        tests.append(f"if (!({readable})) {callback}(32'hffffffff, scope); "
                     f"else if ({expression}) {callback}(event_{index}, scope);")
    ids = "".join(f", input int event_{i}" for i in range(len(rules)))
    return f'''
class {name} #(type T=logic);
  static function automatic T value(input T v, input int scope, input bit observed{ids});
    logic signed [$bits(T):0] sampled;
    sampled = (T'(-1) < T'(0)) ? $signed(v) : $signed({{1'b0, v}});
    if (observed) begin {" ".join(tests)} end
    return v;
  endfunction
endclass
'''


def read_package(definitions, callback_declaration):
    return (f'package {READ_GOAL_PACKAGE};\n' + callback_declaration
            + "".join(definitions) + "endpackage\n")
