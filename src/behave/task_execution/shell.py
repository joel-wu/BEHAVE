"""The solver's ordinary shell, using the shared EDA installation and public docs."""

from dataclasses import replace
from importlib.resources import files

from behave.agent.commands import CommandRunner, shell_commands
from behave.isolation import read_only
from behave.sim import api

from .solver_settings import guide_names


def development_shell(limits, *, setting: str = "joint", cpu_endpoint=None,
                      output_preview_bytes: int | None = None,
                      extra_guides: tuple[str, ...] = ()) -> CommandRunner:
    documents = files("behave.agent").joinpath("prompts", "shared", "guides")
    for name in extra_guides:
        if not name.isidentifier() or not documents.joinpath(name + ".md").is_file():
            raise ValueError(f"Unknown shared development guide: {name}")
    names = tuple(dict.fromkeys((*guide_names(setting), *extra_guides)))
    shell = api.prepare_development_environment(shell_commands(limits).template)
    mounts = tuple(read_only(str(documents.joinpath(name + ".md")), f"/docs/{name}.md",
                             reuse_attestation=True) for name in names)
    return CommandRunner(replace(
        shell, read_only=(*shell.read_only, *mounts),
    ), writable_workspace=True, cpu_endpoint=cpu_endpoint, output_preview_bytes=output_preview_bytes)
