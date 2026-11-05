# Prompts

| Directory | Used by |
| --- | --- |
| `shared/` | Task introductions, development guides, shell tools and turn feedback |
| `solver/` | Evaluation and training: joint, RTL-only and Sim-assisted settings |
| `ppa/` | PPA objectives and measurement tools, sharing solver instructions |
| `synthesis/` | Task acquisition, construction and review agents |
| `fixtures/` | Interface and connectivity tests |

`agent/prompting.py` loads templates with explicit `${name}` substitutions.
`task_execution/solver_settings.py` assembles solver prompts; training and
evaluation share them. Guides are mounted under `/docs` for the agent to read.
Tool schemas, permissions and reference visibility are defined in code.

See [PPA prompts](ppa/README.md) for the optimization workflow.
