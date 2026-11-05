"""Serializable execution capabilities for fixed construction workers."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import fields as dataclass_fields
from typing import TYPE_CHECKING

from behave.agent.attempt import AttemptSpec
from behave.agent.process_model import ModelCommand
from behave.core import runtime_defaults as defaults
from behave.core.records import canonical_json, fields, parse_json
from behave.task_execution.testbench import TBPolicy

if TYPE_CHECKING:
    from behave.isolation.model import ResourceLimits


@dataclass(frozen=True, slots=True)
class RolePolicy:
    model: ModelCommand
    # Retained to decode historical receipts, not executable Syn capabilities.
    python_limits: ResourceLimits | None = None
    cocotb_policy: TBPolicy | None = None
    log_bytes: int = defaults.LOG_RETAIN_BYTES
    shell_limits: ResourceLimits | None = None
    output_preview_bytes: int | None = defaults.SHELL_OUTPUT_PREVIEW_BYTES

    def __post_init__(self) -> None:
        if type(self.model) is not ModelCommand:
            raise TypeError("role execution requires ModelCommand")
        from behave.isolation.model import ResourceLimits

        for limits in (self.python_limits, self.shell_limits):
            if limits is not None and type(limits) is not ResourceLimits:
                raise TypeError("command execution requires ResourceLimits")
        if self.python_limits is not None and self.shell_limits is not None:
            raise ValueError("choose one run_command implementation: Python or Bash")
        if self.cocotb_policy is not None:
            from behave.task_execution.development import cocotb_tool

            cocotb_tool(self.cocotb_policy)
        if type(self.log_bytes) is not int or self.log_bytes < 1:
            raise ValueError("role log limit must be positive")

    def check_attempt(self, attempt: AttemptSpec) -> None:
        if type(attempt) is not AttemptSpec:
            raise TypeError("role policy requires AttemptSpec")
        if attempt.model_policy.get("transport") != self.model.identity:
            raise ValueError("construction attempt must bind its model transport")

    def check_execution(self) -> None:
        if self.python_limits is not None or self.cocotb_policy is not None:
            raise ValueError("Syn uses Bash only: replace python_limits/cocotb_policy with shell_limits")
        if self.shell_limits is None:
            raise ValueError("Syn runtime must configure shell_limits")

    def as_dict(self) -> dict:
        row = {"model": self.model.as_dict(), "log_bytes": self.log_bytes,
                "python_limits": None if self.python_limits is None else self.python_limits.as_dict(),
                "cocotb_policy": None if self.cocotb_policy is None else parse_json(self.cocotb_policy.to_bytes())}
        if self.shell_limits is not None:
            row["shell_limits"] = self.shell_limits.as_dict()
        if self.output_preview_bytes is not None:
            row["output_preview_bytes"] = self.output_preview_bytes
        return row

    @classmethod
    def from_dict(cls, value: object) -> RolePolicy:
        row = fields(value, {"model", "log_bytes", "python_limits", "cocotb_policy"}
                     | ({"shell_limits"} if "shell_limits" in value else set())
                     | ({"output_preview_bytes"} if "output_preview_bytes" in value else set()))
        python = None
        if row["python_limits"] is not None:
            from behave.isolation.model import ResourceLimits

            python = ResourceLimits(**fields(row["python_limits"], {item.name for item in dataclass_fields(ResourceLimits)}))
        tb = None if row["cocotb_policy"] is None else TBPolicy.from_bytes(canonical_json(row["cocotb_policy"]))
        shell = None
        if "shell_limits" in row:
            from behave.isolation.model import ResourceLimits

            shell = ResourceLimits(**fields(row["shell_limits"], {item.name for item in dataclass_fields(ResourceLimits)}))
        return cls(ModelCommand.from_dict(row["model"]), python, tb, row["log_bytes"], shell,
                   row.get("output_preview_bytes"))


@dataclass(frozen=True, slots=True)
class ProcessRoles:
    author: RolePolicy
    reader: RolePolicy
    source_review: RolePolicy | None = None

    def __post_init__(self) -> None:
        if type(self.author) is not RolePolicy or type(self.reader) is not RolePolicy:
            raise TypeError("author and reader require fixed role policies")
        if self.source_review is not None and type(self.source_review) is not RolePolicy:
            raise TypeError("source reviewer requires a fixed role policy")

    def as_dict(self) -> dict:
        return {"author": self.author.as_dict(), "reader": self.reader.as_dict(),
                "source_review": None if self.source_review is None else self.source_review.as_dict()}

    @classmethod
    def from_dict(cls, value: object) -> ProcessRoles:
        row = fields(value, {"author", "reader", "source_review"})
        return cls(RolePolicy.from_dict(row["author"]), RolePolicy.from_dict(row["reader"]),
                   None if row["source_review"] is None else RolePolicy.from_dict(row["source_review"]))


@dataclass(frozen=True, slots=True)
class RtlBuildPolicy:
    """Optional, bounded implementation of a frozen task; not task-author authority."""

    attempt: AttemptSpec
    execution: RolePolicy
    max_revisions: int = defaults.SYN_RTL_REVISIONS

    def __post_init__(self):
        if type(self.attempt) is not AttemptSpec or self.attempt.role != "rtl_author":
            raise TypeError("RTL construction requires an rtl_author attempt")
        if type(self.execution) is not RolePolicy:
            raise TypeError("RTL construction requires a fixed process policy")
        self.execution.check_attempt(self.attempt)
        if type(self.max_revisions) is not int or self.max_revisions < 1:
            raise ValueError("RTL revision limit must be positive")

    def as_dict(self):
        return {"attempt": self.attempt.as_dict(), "execution": self.execution.as_dict(),
                "max_revisions": self.max_revisions}

    @classmethod
    def from_dict(cls, value):
        row = fields(value, {"attempt", "execution", "max_revisions"})
        return cls(AttemptSpec.from_dict(row["attempt"]), RolePolicy.from_dict(row["execution"]), row["max_revisions"])
