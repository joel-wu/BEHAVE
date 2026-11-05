"""ChipMATE's original multi-pair loop and cycle-by-cycle cross-verification."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from behave.agent.attempt import AttemptInterrupted
from behave.core.paths import scratch_root
from behave.core.records import canonical_json
from behave.isolation import python_package
from behave.sim import api

from .upstream import module, source_root


@dataclass(frozen=True)
class ChipmateSettings:
    # Paper Table 1. Package exploratory defaults are N=10, T=5, 30 stimuli.
    pairs: int = 3
    turns: int = 3
    stimuli: int = 1000
    temperature: float = 1.0
    top_p: float = 0.95
    seed: int = 42


class _Backend:
    def __init__(self, calls, role, top_p):
        self.calls, self.role, self.top_p = calls, role, top_p

    def generate(self, prompt, n, temperature):
        def one(_):
            return self.calls.generate(self.role, ({"role": "user", "content": prompt},),
                                       temperature=temperature, top_p=self.top_p).content
        with ThreadPoolExecutor(max_workers=n) as pool:
            return list(pool.map(one, range(n)))


def run(*, task_id, question, interface, calls, sandbox, settings=ChipmateSettings()):
    """interface is a public port-only declaration, never reference RTL.

    The hook moves the pinned cross_verify, with documented fixes, into the
    sandbox. It does not replace it with Sim or a BEHAVE reference model.
    """
    upstream = module("chipmate", "inference")
    package = python_package(str(source_root("chipmate") / "chipmate"), "/method/chipmate")

    def compare(ref_sv, dut_sv, dut_py, **options):
        packet = dict(ref_sv=ref_sv, dut_sv=dut_sv, dut_py=dut_py, **options)
        with TemporaryDirectory(prefix="behave-chipmate-", dir=scratch_root()) as temporary:
            workspace = Path(temporary)
            (workspace / "input.json").write_bytes(canonical_json(packet))
            code = (
                "import json,sys; from pathlib import Path; sys.path.insert(0,'/method'); "
                "from chipmate.cross_verify import cross_verify; "
                "result=cross_verify(**json.loads(Path('input.json').read_text())); "
                "Path('comparison.json').write_text(json.dumps(result))"
            )
            execution, _, _ = sandbox.execute(
                workspace, api.ToolCommand("cross-verify", ("/tools/python", "-c", code)),
                outputs=("comparison.json",), mounts=(package,))
            if "comparison.json" not in execution.outputs:
                raise AttemptInterrupted("chipmate-comparison-unavailable")
            return json.loads(execution.outputs["comparison.json"])

    return upstream.run_problem(task_id=task_id, question=question, ref_sv=interface,
                                v_backend=_Backend(calls, "verilog", settings.top_p),
                                p_backend=_Backend(calls, "python", settings.top_p),
                                n=settings.pairs, max_turns=settings.turns,
                                num_verify_tests=settings.stimuli,
                                temperature=settings.temperature, seed=settings.seed,
                                verifier=compare)
