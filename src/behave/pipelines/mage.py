"""MAGE's original no-golden workflow with shared model and sandbox transport."""

from dataclasses import dataclass

from .upstream import module


@dataclass(frozen=True)
class MageSettings:
    sim_retries: int = 4
    candidates: int = 20
    selected_candidates: int = 2
    temperature: float = 0.85
    top_p: float = 0.95
    parallel_requests: int = 10


DEFAULT_SETTINGS = MageSettings()


def run(*, question, workspace, calls, sandbox, settings=DEFAULT_SETTINGS):
    from .mage_transport import _TOOLS, ModelBinding

    upstream = module("mage", "agent")
    agent = upstream.TopAgent(ModelBinding(calls, settings.temperature, settings.top_p,
                                          settings.parallel_requests))
    agent.sim_max_retry = settings.sim_retries
    agent.rtl_max_candidates = settings.candidates
    agent.rtl_selected_candidates = settings.selected_candidates
    # _run creates the exact original roles and executes run_instance. Avoid
    # run()'s process-global stdout/log-directory mutation in concurrent tasks.
    workspace.mkdir(parents=True, exist_ok=True)
    agent.output_dir_per_run = str(workspace)
    agent.golden_tb_path = agent.golden_rtl_blackbox_path = None
    token = _TOOLS.set((sandbox, workspace))
    try:
        passed, rtl = agent._run(question)
    finally:
        _TOOLS.reset(token)
    return {"verilog": "" if rtl.startswith("Exception:") else rtl,
            "matched": passed, "upstream_error": rtl if rtl.startswith("Exception:") else None}
