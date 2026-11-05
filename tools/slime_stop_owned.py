"""Compatibility entry for the shared authenticated training stop helper.

Direct execution uses this checkout's standalone module, including with isolated
system Python. No allocation is cancelled and a signal is not cleanup evidence.
"""

if __name__ == "__main__":
    import runpy
    from pathlib import Path

    runpy.run_path(str(Path(__file__).resolve().parents[1] / "src/behave/train/job_stop.py"),
                   run_name="__main__")
else:
    from behave.train.job_stop import execute_child, main, stop

    __all__ = ["execute_child", "main", "stop"]
