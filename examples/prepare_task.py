"""Prepare the passthrough task for agent evaluation without a dataset download."""

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from public_sim_consumer import REFERENCE_SOURCE, example_contract

from behave.core.paths import scratch_root
from behave.data.native_release import prepare

SPEC = """# Byte passthrough

Implement a byte-wide ready/valid passthrough.

## Interface

### RTL interface

The module is `public_consumer_passthrough`. It has `clk`, active-high `rst`,
`in_valid`, `in_ready`, `in_a[7:0]`, `out_valid`, `out_ready` and `out_y[7:0]`.

### Behavior interface

```python
class Identity:
    def reset(self): ...
    def process(self, in_a): ...
```

`process` takes an unsigned byte and returns `{"out_y": in_a}`.

## Modules

A single passthrough module; no submodules are needed.

## Functional Behavior

Forward each input byte unchanged. Outside reset, `out_valid = in_valid`,
`in_ready = out_ready` and `out_y = in_a`. There is no internal buffering.

## Reset Behavior

While `rst` is asserted, deassert `in_ready` and `out_valid`. There is no
persistent state to restore.
"""


def prepare_example(output: Path) -> dict:
    with TemporaryDirectory(prefix="behave-example-", dir=scratch_root()) as directory:
        root = Path(directory)
        task = root / "data/problems/example/passthrough"
        task.mkdir(parents=True)
        (task / "spec.md").write_text(SPEC, encoding="utf-8")
        (task / "behavior.py").write_text(REFERENCE_SOURCE, encoding="utf-8")
        (task / "interface.json").write_text(
            json.dumps(example_contract().as_dict()), encoding="utf-8")
        tasks = root / "tasks.txt"
        tasks.write_text("passthrough\n", encoding="utf-8")
        return prepare(root, output.resolve(), [tasks], workers=1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new task-release directory")
    args = parser.parse_args()
    print(json.dumps(prepare_example(args.output), indent=2))


if __name__ == "__main__":
    main()
