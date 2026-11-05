# Datasets

| Dataset | Tasks | Split |
| --- | ---: | --- |
| BEHAVE | 600 | 540 train, 60 eval |
| VerilogEval | 156 | external eval |
| RTLLM | 50 | external eval |
| CVDP | 115 | external eval |

Tasks are in `problems/<domain>/<task_id>/`: `spec.md`, `interface.json`,
`behavior.py` and `rtl.sv`. Reference implementations are not shown to the agent.

Split lists are in `splits/authored600_v2/`; the 60-task training seed subset is
`splits/self_improvement_seed60_v2/seed.txt`.

Prepare BEHAVE-Eval from the repository root:

```bash
uv run behave-data freeze-native work/behave-eval-inputs \
  --repository . --tasks data/splits/authored600_v2/eval.txt
```

See [evaluation](../docs/eval.md) for running and scoring.

VerilogEval, RTLLM and CVDP are adapted to BEHAVE's task format.
Sources and license notices: [SOURCES.csv](SOURCES.csv), [licenses/](licenses/)
and task-local attachments.

Task-local `source.json` files link to pinned upstream sources. Their
`license_file` paths are relative to the repository root.
