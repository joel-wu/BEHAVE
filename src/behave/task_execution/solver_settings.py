"""Eval/Train solver settings and the development fragments also used by PPA."""

from behave.agent.prompting import render

_GUIDES = {
    "joint": ("development", "behavior", "fp_lib", "memory"),
    "rtl_only": ("development", "fp_lib", "memory"),
    "joint_sim": ("development", "behavior", "fp_lib", "memory"),
    "rtl_sim": ("development", "fp_lib", "memory"),
}


def guide_names(setting: str) -> tuple[str, ...]:
    if setting not in _GUIDES:
        raise ValueError(f"unknown solver setting: {setting!r}")
    return _GUIDES[setting]


def prompt_path(setting: str, name: str) -> str:
    guide_names(setting)
    return f"solver/{setting}/{name}"


def development_instructions(setting: str) -> str:
    """Shared implementation AND verification duties, without scoring or final action."""
    if setting == "joint":
        return render("shared/development/joint_testbench",
                      behavior_model=render("shared/development/behavior_model"))
    if setting == "rtl_only":
        return render("shared/development/rtl_only_development")
    if setting == "joint_sim":
        return "\n\n".join((render("shared/development/joint_development",
                      behavior_model=render("shared/development/behavior_model")),
                      render("shared/development/joint_sim_verification")))
    if setting == "rtl_sim":
        return "\n\n".join((render("shared/development/rtl_development"),
                             render("shared/development/rtl_sim_verification")))
    raise ValueError(f"unknown solver setting: {setting!r}")


def development_environment(setting: str) -> str:
    """Shared installed-guide index; does not require a submission or TB."""
    names = guide_names(setting)
    return render("shared/development/environment", behavior_reference=(
        render("shared/development/behavior_reference") + "\n" if "behavior" in names else ""))


def system_variables(setting: str) -> dict[str, str]:
    guide_names(setting)
    return {"development": development_instructions(setting)}


def render_prompt(setting: str, name: str) -> str:
    """Explicit composition, with the same rendered text for existing solvers."""
    values = system_variables(setting) if name == "system" else {}
    if name == "environment" and setting in {"joint_sim", "rtl_sim"}:
        values = {"development_environment": development_environment(setting)}
    return render(prompt_path(setting, name), **values)


def task_variables(task, files, *, environment_docs: str = "") -> dict[str, str]:
    """One task opening and attachment index for ordinary and PPA development."""
    paths = [file.reference.path for file in files if file.reference.path != task.spec_path]
    section = (render("shared/task/public_files", paths="\n".join(f"- `{path}`" for path in paths))
               + "\n\n" if paths else "")
    return {"task_id": task.task_id, "spec_path": task.spec_path,
            "public_files_section": section, "environment_docs": environment_docs}


def submission_components(setting: str) -> tuple[str, ...]:
    guide_names(setting)
    return {"joint": ("behavior", "rtl", "testbench"), "rtl_only": ("rtl",),
            "joint_sim": ("behavior", "rtl"), "rtl_sim": ("rtl",)}[setting]


def comparison_source(setting: str) -> str | None:
    guide_names(setting)
    return {"joint_sim": "agent", "rtl_sim": "provided"}.get(setting)
