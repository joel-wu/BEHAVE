"""Runtime presentation options shared without importing execution backends."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class DiagnosticOptions:
    max_runs: int | None = None
    max_events_per_run: int | None = None
    max_faults_per_run: int | None = None

    def __post_init__(self):
        for name, value in asdict(self).items():
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{name} must be a nonnegative integer or null")
