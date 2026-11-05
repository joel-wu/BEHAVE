"""Source-owned language violations reported by the shared language checker.

These are reviewed SystemVerilog rules, not a classification of arbitrary
frontend errors. Only a completed compiler check whose errors all identify
these rules produces a rejection. Warnings, prose, unsupported features and
interrupted tools never establish one. The caller still owns candidate versus
reference attribution.
"""

from dataclasses import dataclass
from enum import Enum

from ..core.refusal import Refusal
from ..strict_json import strict_json_loads

VERSION = "rtl-source-rules/6"
DIAGNOSTICS_FILE = "source-diagnostics.json"
TYPED_DIAGNOSTICS_FILE = "source-check.json"
REJECTION_FILE = "source-rejection.json"
REJECTION_EXIT = 65  # Shared frontend protocol, not a resource allowance.
SLANG_ANALYSIS_REJECTION = 5


class SourceRule(str, Enum):
    EXCLUSIVE_PROCEDURAL_WRITER = "MultipleAlwaysAssigns"
    SINGLE_CONTINUOUS_VARIABLE_DRIVER = "MultipleContAssigns"
    NO_MIXED_VARIABLE_DRIVERS = "MixedVarAssigns"
    AUTOMATIC_FROM_STATIC_INITIALIZER = "AutoFromStaticInit"
    ASSIGNMENT_TYPE = "BadAssignment"
    IMPLICIT_CONVERSION = "NoImplicitConversion"
    CONDITIONAL_TYPES = "BadConditionalExpression"
    INTEGER_CAST_TYPE = "BadIntegerCast"
    REVERSED_RANGE = "RangeSelectReversed"
    SELECT_AFTER_RANGE = "SelectAfterRangeSelect"
    DPI_RETURN_TYPE = "InvalidDPIReturnType"
    PROCEDURAL_ASSIGN_TARGET = "BadProceduralAssign"
    PROCEDURAL_NET_ASSIGNMENT = "AssignToNet"
    NONPROCEDURAL_COPYBACK_CALL = "NonProceduralFuncArg"
    UNDECLARED_IDENTIFIER = "UndeclaredIdentifier"
    EXPECTED_ENUM_BASE = "ExpectedEnumBase"
    EXPECTED_SIGNED_INTEGER_BASE = "ExpectedIntegerBaseAfterSigned"
    IMPLICIT_TYPE = "ImplicitNotAllowed"
    EXPECTED_IDENTIFIER = "ExpectedIdentifier"
    EXPECTED_DECLARATOR = "ExpectedDeclarator"
    EXPECTED_EXPRESSION = "ExpectedExpression"
    EXPECTED_STATEMENT = "ExpectedStatement"
    EXPECTED_TOKEN = "ExpectedToken"
    EXPRESSION_NOT_STATEMENT = "ExprNotStatement"


@dataclass(frozen=True)
class SourceViolation:
    rule: SourceRule
    symbol: str | None
    location: str
    message: str

    def __post_init__(self):
        if (type(self.rule) is not SourceRule
                or self.symbol is not None and (type(self.symbol) is not str or not self.symbol)
                or type(self.location) is not str or not self.location
                or type(self.message) is not str or not self.message):
            raise ValueError("source evidence needs a rule, location and compiler explanation")

    def as_dict(self):
        return {"rule": self.rule.value, "symbol": self.symbol,
                "location": self.location, "message": self.message}


class SourceRuleRejected(Exception):
    def __init__(self, violations):
        self.violations = tuple(violations)
        if not self.violations or any(type(item) is not SourceViolation for item in self.violations):
            raise ValueError("a source rejection needs compiler-owned violations")
        super().__init__("source violates SystemVerilog language rules")

    def as_dict(self):
        return {"version": VERSION, "violations": [item.as_dict() for item in self.violations]}

    def reason(self, execution):
        return Refusal("rtl-source-rule-violation", str(self),
                       source_rules=self.as_dict(), execution=execution.as_dict())


def from_compiler(payload: bytes) -> SourceRuleRejected | None:
    """Called only after the client's ordinary check-rejection exit, on original RTL."""
    document = strict_json_loads(payload, what="Slang source diagnostics")
    if type(document) is not list or any(type(row) is not dict for row in document):
        raise ValueError("Slang diagnostics must be a list of diagnostic records")
    violations = []
    for row in document:
        if row.get("severity") in {"note", "warning"}:
            continue
        if row.get("severity") != "error":
            return None
        try:
            rule = SourceRule(row.get("code"))
        except ValueError:
            return None  # Mixed/unrecognized errors are not this proof of rejection.
        violations.append(SourceViolation(rule, row.get("symbolPath"), row.get("location"), row.get("message")))
    return SourceRuleRejected(violations) if violations else None


def read_rejection(payload: bytes) -> SourceRuleRejected:
    document = strict_json_loads(payload, what="source rejection")
    if (type(document) is not dict or set(document) != {"version", "violations"}
            or document["version"] != VERSION or type(document["violations"]) is not list):
        raise ValueError("invalid source rejection record")
    violations = []
    for row in document["violations"]:
        if type(row) is not dict or set(row) != {"rule", "symbol", "location", "message"}:
            raise ValueError("invalid source violation")
        violations.append(SourceViolation(SourceRule(row["rule"]), row["symbol"], row["location"], row["message"]))
    return SourceRuleRejected(violations)
