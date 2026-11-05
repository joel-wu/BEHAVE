"""Minimal dependency closure for importing existing eval admission records."""
from pathlib import Path


def recovery_dependency_overrides(base, repository):
    base, repository = Path(base), Path(repository)
    prefix = "src/behave/task_execution/"
    overrides = {prefix + name: (repository / (prefix + name)).read_bytes()
                 for name in ("batch_plan.py", "batch_report.py")}
    # Retain the frozen resource/simulation behavior, including older CPU APIs.
    patches = {
        "batch.py": (
            "        pending_jobs = tuple((index, job) for index, job in enumerate(plan.jobs) if index not in completed)\n",
            "        recorded = {ref for ref, _ in plan.recorded_admissions}\n"
            "        if any(BlobRef.of(job.to_bytes()) in recorded for _, job in pending_jobs):\n"
            "            raise RecordError(\"batch-recovery-invalid\", \"recorded admission requires an imported or replayed case\")\n"),
        "rejudge.py": (
            '    raw_plan["jobs"] = [job for job, _ in chosen]\n',
            '    if "recorded_admissions" in raw_plan:\n'
            '        keys = {row["run_key"] for _, row in chosen}\n'
            '        raw_plan["recorded_admissions"] = [\n'
            '            item for item in raw_plan["recorded_admissions"] if item["job"]["sha256"] in keys]\n'),
    }
    for name, (anchor, addition) in patches.items():
        path = prefix + name
        source = (base / path).read_text()
        if anchor + addition not in source:
            if source.count(anchor) != 1:
                raise ValueError("unknown frozen recovery dependency: " + path)
            source = source.replace(anchor, anchor + addition, 1)
        overrides[path] = source.encode()
    return overrides
