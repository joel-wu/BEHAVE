"""PPA objectives share their text; only available development feedback varies."""

from behave.agent.prompting import render


def solver_setting(behavior_source, *, sim_feedback=True):
    """Choose the existing development workflow, not just its Behavior author."""
    if behavior_source not in {"agent", "provided"} or type(sim_feedback) is not bool:
        raise ValueError("invalid PPA Behavior source or Sim feedback setting")
    if sim_feedback:
        return "joint_sim" if behavior_source == "agent" else "rtl_sim"
    return "joint" if behavior_source == "agent" else "rtl_only"


def feedback_instructions(sim_feedback=True):
    return render('ppa/development/sim_feedback' if sim_feedback else 'ppa/development/no_sim_feedback')


def render_objective(primary_target_ns=None, *, parallelism_lanes=None, sim_feedback=True):
    if (primary_target_ns is None) == (parallelism_lanes is None):
        raise ValueError('choose a time bound or parallelism guidance')
    goal = (render('ppa/objectives/parallelism', parallelism_lanes=str(parallelism_lanes))
            if parallelism_lanes is not None else
            render('ppa/objectives/time_bound', primary_target_ns=str(primary_target_ns)))
    return render('ppa/objective', search_objective=goal,
                  measurement_instructions=feedback_instructions(sim_feedback))


def bind_objective(prompt, experiment, replica):
    from .config import replica_objective

    assignment = replica_objective(experiment, replica)
    goal = (render('ppa/objectives/parallelism', parallelism_lanes=str(assignment['parallelism_lanes']))
            if assignment['objective_mode'] == 'parallelism' else
            render('ppa/objectives/time_bound', primary_target_ns=str(assignment['target_ns'])))
    prompt = prompt.replace('${search_objective}', goal)
    if assignment['objective_mode'] == 'parallelism':
        if '${primary_target_ns}' in prompt or '${primary_target_index}' in prompt:
            raise ValueError('time-bound prompt used for parallelism experiment')
        return prompt.replace('${parallelism_lanes}', str(assignment['parallelism_lanes']))
    if '${parallelism_lanes}' in prompt:
        raise ValueError('parallelism prompt used for time-bound experiment')
    return prompt.replace('${primary_target_index}', str(assignment['objective_index'])).replace(
        '${primary_target_ns}', str(assignment['target_ns']))


def bind_feedback(prompt, *, sim_feedback=True):
    return prompt.replace('${measurement_instructions}', feedback_instructions(sim_feedback))
