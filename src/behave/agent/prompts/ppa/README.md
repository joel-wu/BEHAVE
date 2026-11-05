# PPA prompts

PPA reuses solver development instructions and adds an optimization objective:

- [objective.md](objective.md): area and completion-time goals.
- [context.md](context.md): workload and technology settings.
- [tools/](tools/): candidate capture, measurement and completion.

`ppa/prompting.py` selects the shared development setting from the Behavior
source and `sim_feedback` option. Without Sim, agents validate their own
Behavior and RTL with a testbench; with Sim, they use `compare_rtl`.
`measure_ppa` measures a frozen candidate and `finish_search` ends the search.

Workloads and sampling settings live in `configs/ppa/`. `behave.ppa prepare`
freezes these inputs; `behave.ppa compare` performs final evaluation.
