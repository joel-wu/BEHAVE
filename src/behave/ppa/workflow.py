"""Tool-assisted PPA development using the shared agent loop and Sim comparison.

The public feedback projection lives here. Native services return private
records, never tool responses. Ordinary Eval never installs these capabilities.
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from behave.agent.attempt import AttemptInterrupted
from behave.agent.local_workspace import LocalWorkspace
from behave.agent.loop import run_attempt
from behave.agent.prompting import render, role_messages
from behave.agent.replay import Tape
from behave.agent.run_store import JournalDirectory
from behave.agent.tools import Tool, ToolResult, file_tools, object_schema
from behave.agent.trajectory import trajectory
from behave.core.artifacts import BlobRef, FileData, FileRef, LoadedSource, SourceArtifact, read_verified
from behave.core.paths import scratch_root
from behave.core.records import canonical_json, fields, parse_json, text, thaw_json
from behave.task_execution.comparison import ComparisonInputs
from behave.task_execution.feedback import comparison_feedback
from behave.task_execution.judge_policy import JudgePolicy
from behave.task_execution.judgment import ComponentJudgment
from behave.task_execution.sim_tool import ComparisonToolInputs, comparison_tool
from behave.task_execution.solver import solver_input_files
from behave.task_execution.solver_settings import development_environment, development_instructions, task_variables

from .records import pack_result, read_document, read_log, write_document
from .records import union_pool as _union
from .report import frontier  # noqa: F401 -- historical execution/reporting import


def eda_feedback(eda, objects=None, *, prefix):
    """Return core measurements and workspace files for the full EDA diagnostics."""
    if eda is None:
        return None, ()
    public = {key: eda[key] for key in ("status", "area_um2", "timing")}
    if 'stage' in eda:
        public['stage'] = eda['stage']
    if "mapping_repair" in eda:
        public["mapping_repair"] = {key: eda["mapping_repair"][key]
                                    for key in ("completed", "max_fanout", "buffer_gain")}
        if 'physical_profile' in eda['mapping_repair']:
            public['mapping_repair']['physical_profile'] = eda['mapping_repair']['physical_profile']
    power = eda.get("power")
    public["power"] = (None if power is None else
        {key: power[key] for key in ("status", "total_w", "units", "reason") if key in power})
    if "failed_stage" in eda:
        public["failed_stage"] = eda["failed_stage"]
        stage = eda.get("stages", {}).get(eda["failed_stage"], {})
        public["failure"] = {key: stage[key] for key in (
            "reason", "budget_seconds", "elapsed_seconds", "returncode", "last_operation") if key in stage}
    if eda.get("code") is not None:
        public["code"] = eda["code"]
    timing = eda.get("timing") or {}
    from .timing_feedback import critical_paths

    log = read_log(objects, eda)
    paths = critical_paths(log, capacitance_unit=eda.get('report_units', {}).get('capacitance', 'pF'))
    if paths:
        public["critical_paths"] = paths
    lines = ["# EDA report", "", f"Status: {eda['status']}",
             f"Timing: {timing.get('status', 'unavailable')}"]
    for label, value, unit in (("Mapped cell area", eda["area_um2"], "µm²"),
                              ("Setup slack", timing.get("setup_slack_ns"), "ns"),
                              ("Hold slack", timing.get("hold_slack_ns"), "ns")):
        lines.append(f"{label}: " + ("unavailable" if value is None else f"{value} {unit}"))
    if "failed_stage" in eda:
        lines.append(f"Failed stage: {eda['failed_stage']}")
        failure = public["failure"]
        for key, label, unit in (("reason", "Reason", ""),
                                 ("budget_seconds", "Stage wall-time limit", " s"),
                                 ("elapsed_seconds", "Stage elapsed wall time", " s"),
                                 ("returncode", "Exit code", ""),
                                 ("last_operation", "Last ABC operation started", "")):
            if key in failure:
                lines.append(f"{label}: {failure[key]}{unit}")
    if "code" in public:
        lines.append(f"Reason: {public['code']}")
    for path in paths:
        lines.extend(["", f"## Critical {path['path_type']} path", "",
                      f"{path['startpoint']} → {path['endpoint']}",
                      f"Slack: {path['slack_ns']} ns"])
        for pin in path["largest_increments"]:
            suffix = (f", fanout {pin['fanout']}, load {pin['capacitance_pf']} pF, slew {pin['slew_ns']} ns"
                      if "fanout" in pin else "")
            lines.append(f"- {pin['pin']} ({pin['cell']}): {pin['delay_ns']} ns{suffix}")
        if "highest_fanout" in path:
            pin = path["highest_fanout"]
            lines.append(f"Highest path fanout: {pin['pin']} drives {pin['fanout']} loads.")
    lines.extend(["", "Full tool log: `eda.log`", "", "## Measurement details", "", "```json",
                  json.dumps({key: eda[key] for key in ("stage", "stages", "mapping_repair", "power") if key in eda},
                             indent=2, ensure_ascii=False), "```", ""])
    files = tuple(FileData(FileRef(f"{prefix}/{name}", BlobRef.of(data)), data) for name, data in (
        ("report.md", "\n".join(lines).encode()), ("eda.log", log.encode())))
    return {**public, "report": files[0].reference.path, "log": files[1].reference.path}, files


def candidate_identity(source, reference=None):
    """A mutable self-reference makes the candidate a source pair, not RTL alone."""
    payload = source if reference is None else {"rtl": source, "behavior": reference}
    return BlobRef.of(canonical_json(payload)).sha256


def _seal_journals(run, *names):
    """Bind explicit journal heads and dependencies using the shared CAS API."""
    heads, references = {}, []
    for name in names:
        head, closure = run.seal(name)
        heads[name] = None if head is None else head.as_dict()
        references.extend(closure)
    return heads, tuple(dict.fromkeys(references))


class SearchArchive:
    """Private external-action journal. Unsettled calls forbid recovery/takeover."""

    def __init__(self, run, task, experiment, native, *, comparison=None, sim_feedback=True):
        self.run, self.task, self.experiment, self.native = run, task, experiment, native
        self.sim_feedback = sim_feedback
        self.comparison = comparison or ComparisonToolInputs(
            task.contract, task.run_policy, task.judge_policy, task.reference_behavior)
        self.journal = run.journal("measurements")
        self.events = self.journal.load()
        self.candidates, self.pool = {}, ()
        self.pending_refs = []
        for event in self.events:
            if event.kind == "settled":
                row = read_document(run.objects, BlobRef.from_dict(thaw_json(event.payload["record"])))
                self.candidates = row["candidates"]
                self.pool = tuple(read_document(run.objects, BlobRef.from_dict(row["pool_ref"])))
        if self.events and self.events[-1].kind == "requested":
            raise AttemptInterrupted("ppa-measurement-outcome-unknown")
        self.calls = sum(not item["final"] for row in self.candidates.values() for item in row["history"])

    def append(self, kind, payload, refs=()):
        event = self.journal.append(self.events, kind, payload, refs)
        self.events = (*self.events, event)

    def reference_for(self, row, *, final):
        if final:
            return self.task.reference_behavior
        selected = row.get("source_reference")
        if selected is None:
            return self.comparison.reference_behavior
        artifact = SourceArtifact.from_dict(selected)
        return LoadedSource(artifact, tuple(
            FileData(file, read_verified(self.run.objects, file.content)) for file in artifact.files))

    def inputs_for(self, candidate, *, final=False, mapped=False):
        row = self.candidates[candidate]
        image = row.get("eda", {}).get("simulation_source") if mapped else row["source"]
        if image is None:
            return None
        artifact = SourceArtifact.from_dict(image)
        source = LoadedSource(artifact, tuple(FileData(f, read_verified(self.run.objects, f.content))
                                             for f in artifact.files))
        return ComparisonInputs("rtl", source, self.reference_for(row, final=final), self.task.contract,
            self.task.run_policy, self.task.judge_policy,
            reference_visible=not final and self.comparison.diagnostics_visible)

    def invoke(self, candidate, mode, *, final=False):
        began = time.monotonic()
        inputs = self.inputs_for(candidate, final=final, mapped=mode == "mapped")
        result = ({"score": None, "code": "ppa-mapped-source-unavailable"} if inputs is None else
                  self.native.run(inputs, self.experiment, mode, self.pool, self.run.objects))
        self.record(candidate, mode, result, time.monotonic() - began, final=final)
        return result

    def record(self, candidate, mode, result, elapsed_seconds, *, final=False):
        row = self.candidates[candidate]
        pool_ref, closure = write_document(self.run.objects, list(self.pool))
        self.pending_refs.extend(closure)
        reference = self.reference_for(row, final=final)
        packed, closure = pack_result(self.run.objects, result)
        record, record_closure = write_document(self.run.objects, packed)
        self.pending_refs.extend((*closure, *record_closure))
        self.calls += int(not final)
        if mode in {"check", "final"}:
            row["score"] = result.get("score")
            if final and mode == "check":
                row["verification"] = {key: result.get(key) for key in ("score", "code")}
            elif final and mode == "final" and "verification" in row:
                # The common-pool replay cannot erase an earlier definite
                # failure or claim a missing golden check was conclusive.
                scores = (row["score"], row["verification"]["score"])
                row["score"] = 0 if 0 in scores else None if None in scores else 1
            if "source_reference" in row:
                row["final_score" if final else "development_match"] = row["score"]
        if mode == "mapped":
            row["mapped_score"] = result.get("score")
            row["mapped_performance"] = result.get("performance")
        if mode in {"final", "performance"}:
            # Preparation may fail before workload metrics exist. Preserve its
            # candidate-zero versus infrastructure-no-result attribution.
            row["performance"] = {**result.get("performance", {"score": result.get("score"), "time_ns": None}),
                                  "source": "rtl"}
        if mode == "eda":
            row["eda"] = packed.get("eda", {"stage": self.experiment.as_dict()["stage"],
                "status": "no-result", "area_um2": None, "timing": None, "log": "",
                **({"code": result["code"]} if "code" in result else {})})
            # Optional mapped diagnostics depend on this mapping. RTL workload
            # cycles depend on the captured source, not on a gate-netlist build.
            if "mapped_score" in row:
                row["mapped_score"] = None
            row.pop("mapped_performance", None)
            if row.get("performance", {}).get("source") != "rtl":
                row.pop("performance", None)
        row.setdefault("history", []).append({"mode": mode, "final": final,
            "elapsed_seconds": elapsed_seconds,
            "reference_behavior": reference.artifact.as_dict(),
            "pool": pool_ref.as_dict(), "record": record.as_dict(),
            "result": {key: result[key] for key in ("score", "code") if key in result}})

    def action(self, candidate, mode, *, final=False):
        return self.actions(candidate, (mode,), final=final)[mode]

    def actions(self, candidate, modes, *, final=False):
        """Run independent services together; journal their results on one thread."""
        pool_ref, closure = write_document(self.run.objects, list(self.pool))
        self.append("requested", {"candidate": candidate, "modes": list(modes), "final": final,
                                  "pool": pool_ref.as_dict()}, closure)
        if len(modes) == 1:
            results = {modes[0]: self.invoke(candidate, modes[0], final=final)}
        else:
            measurements = self.native.run_many(self.inputs_for(candidate, final=final),
                self.experiment, modes, self.pool, self.run.objects)
            results = {}
            for mode in modes:
                result, elapsed = measurements[mode]
                self.record(candidate, mode, result, elapsed, final=final)
                results[mode] = result
        if "check" in results:
            # Development uses the normal comparison tool. Common-final replay
            # checks all retained candidates against one frozen pool later.
            self.pool = _union(self.pool, results["check"].get("pool", ()))
        self.snapshot()
        return results

    def snapshot(self):
        """Persist captured sources even when no measurement has been requested."""
        pool_ref, pool_closure = write_document(self.run.objects, list(self.pool))
        snapshot, closure = write_document(self.run.objects,
            {"candidates": self.candidates, "pool_ref": pool_ref.as_dict()})
        refs = (*closure, *pool_closure, *self.pending_refs)
        refs += tuple(f.content for row in self.candidates.values() for f in SourceArtifact.from_dict(row["source"]).files)
        refs += tuple(f.content for row in self.candidates.values() if row.get("source_reference") is not None
                      for f in SourceArtifact.from_dict(row["source_reference"]).files)
        self.append("settled", {"record": snapshot.as_dict()}, tuple(dict.fromkeys(refs)))
        self.pending_refs.clear()

    def tools(self):
        self_reference = self.comparison.reference_behavior is None
        shared = comparison_tool(self.comparison)

        def check(workspace, arguments):
            comparison = self.comparison.capture(workspace, arguments)
            source = comparison.source.artifact
            reference = comparison.reference_behavior.artifact if self_reference else None
            key = candidate_identity(source.as_dict(), None if reference is None else reference.as_dict())
            if key not in self.candidates:
                self.candidates[key] = {"candidate": key, "source": source.as_dict(),
                    "order": len(self.candidates), "history": []}
                if reference is not None:
                    self.candidates[key]["source_reference"] = reference.as_dict()
            for file in (*comparison.source.files, *comparison.reference_behavior.files):
                self.run.objects.put(file.data)
            if not self.sim_feedback:
                self.snapshot()
                return ToolResult({"candidate": key, "status": "captured", "reference_source": self.comparison.source})
            result = self.action(key, "check")
            judgment = ComponentJudgment.from_dict(result["judgment"])
            record = self.candidates[key]["history"][-1]["record"]
            content, files = comparison_feedback(judgment, self.run.objects,
                prefix=f"comparison/{record['sha256']}", diagnostics_visible=self.comparison.diagnostics_visible)
            for file in files:
                workspace.write(file.reference.path, file.data)
            return ToolResult({**content, "candidate": key, "reference_source": self.comparison.source},
                              judgment.closure)

        def measure(workspace, arguments):
            args = fields(arguments, {"candidate"})
            key = text(args["candidate"], "candidate")
            if key not in self.candidates:
                return ToolResult({"error": "unknown-candidate"})
            if not self.sim_feedback:
                self.action(key, "eda")
                row = self.candidates[key]
                record = row["history"][-1]["record"]
                public_eda, files = eda_feedback(row.get("eda"), self.run.objects,
                    prefix=f"eda/{record['sha256']}")
                for file in files:
                    workspace.write(file.reference.path, file.data)
                return ToolResult({"candidate": key, "eda": public_eda})
            measured = self.actions(key, ("eda", "performance"))
            eda_record = self.candidates[key]["history"][-2]["record"]
            workload = measured["performance"]
            row = self.candidates[key]
            eda = row.get("eda")
            public_eda, files = eda_feedback(eda, self.run.objects, prefix=f"eda/{eda_record['sha256']}")
            for file in files:
                workspace.write(file.reference.path, file.data)
            perf = row.get("performance", {"score": None, "time_ns": None})
            comparison = {"status": "unavailable", "code": workload.get("code")}
            evidence = ()
            if "judgment" in workload:
                judgment = ComponentJudgment.from_dict(workload["judgment"])
                record = row["history"][-1]["record"]
                comparison, files = comparison_feedback(judgment, self.run.objects,
                    prefix=f"comparison/{record['sha256']}", diagnostics_visible=self.comparison.diagnostics_visible)
                for file in files:
                    workspace.write(file.reference.path, file.data)
                evidence = judgment.closure
            response = {"candidate": key, "reference_source": self.comparison.source,
                "eda": public_eda, "performance": perf,
                "comparison": comparison}
            if self.experiment.targets_ns:
                response["targets_met"] = [None if perf["time_ns"] is None else (
                    row.get("score") == 1 and perf.get("score") == 1 and
                    (eda or {}).get("status") == "measured" and
                    (row.get("eda", {}).get("timing") or {}).get("status") == "met" and perf["time_ns"] <= t)
                                for t in self.experiment.targets_ns]
            return ToolResult(response, evidence)

        def finish(workspace, arguments):
            fields(arguments, set())
            return ToolResult({"status": "search-finished"}, terminal=True)

        revision = "ppa-tools/13:" + self.experiment.identity + ":" + self.native.identity + ":" + str(self.sim_feedback)
        return (
            Tool(shared.name if self.sim_feedback else "capture_candidate",
                 (shared.description + "\n\n" + render("ppa/tools/capture_candidate")) if self.sim_feedback
                 else render("ppa/tools/register_candidate"),
                 shared.parameters, check, revision),
            Tool("measure_ppa", render("ppa/tools/measure_ppa" if self.sim_feedback else "ppa/tools/measure_eda"),
                 object_schema({"candidate": {"type": "string"}}), measure, revision),
            Tool("finish_search", render("ppa/tools/finish_search"),
                 object_schema({}), finish, revision, terminal=True),
        )


def run_search(*, public, task, experiment, spec, backend, native, directory, prompt: str,
               final_pool: tuple = (), development_tools=(), behavior_source="provided",
               reference_visible=None, provided_behavior=None, sim_feedback=True):
    """No initial RTL. One budgeted agent attempt; final validation gets no feedback.

    ``final_pool`` is trusted retained test data, never added to the agent input.
    This executes one replicate. Cross-method final suites must be frozen and
    shared by the experiment controller before making comparison claims.
    """
    if (public.task_id, public.release, public.revision) != (task.task_id, task.release, task.revision):
        raise ValueError("public and private task revisions differ")
    if type(sim_feedback) is not bool:
        raise ValueError("sim_feedback must be boolean")
    from .prompting import bind_feedback, solver_setting

    prompt = bind_feedback(prompt, sim_feedback=sim_feedback)
    if task.reference_rtl is not None:
        task = replace(task, reference_rtl=None)  # never needed, staged or supplied
    published_judge_policy = task.judge_policy
    task = replace(task, judge_policy=replace(JudgePolicy.from_bytes(task.judge_policy),
                    score_timeout_seconds=experiment.service_seconds).to_bytes())
    if JudgePolicy.from_bytes(task.judge_policy).analysis is not None:
        raise ValueError("PPA requires score-only judge policy")
    if behavior_source not in {"agent", "provided"}:
        raise ValueError("behavior_source must be agent or provided")
    if reference_visible is None:
        reference_visible = behavior_source == "provided"
    if behavior_source == "agent" and (reference_visible or provided_behavior is not None):
        raise ValueError("an agent-authored reference is selected by each check call")
    reference = (provided_behavior or task.reference_behavior) if behavior_source == "provided" else None
    comparison = ComparisonToolInputs(task.contract, task.run_policy, task.judge_policy,
                                     reference, reference_visible)
    from behave.sim import api

    from .performance import endpoints

    contract = api.TaskContract.from_dict(parse_json(task.contract.data))
    for work in experiment.workloads:
        endpoints(contract, work)
    if experiment.timing.clock != contract.clocks[0].clock:
        raise ValueError("PPA timing clock differs from task clock")
    if next(f.reference.content for f in public.files if f.reference.path == public.contract_path) != task.contract.reference.content:
        raise ValueError("public and private task contracts differ")
    setting = solver_setting(behavior_source, sim_feedback=sim_feedback)
    allowed_files = solver_input_files(public, setting=setting)
    allowed_files = (*allowed_files, *comparison.public_files())
    spec = replace(spec, read_only_paths=tuple(f.reference.path for f in allowed_files))
    run = JournalDirectory(directory)
    with run.owner(), TemporaryDirectory(prefix="behave-ppa-rollout-", dir=scratch_root()) as temporary:
        workspace = LocalWorkspace(run.objects, Path(temporary), read_only_paths=spec.read_only_paths)
        tape = Tape(run.journal("workflow"))
        # Configuration dependencies exist even if the agent produces no RTL
        # and no native measurement service is ever invoked.
        task_refs = tuple(run.objects.put(f.data) for f in (task.contract, *task.reference_behavior.files))
        if reference is not None:
            task_refs += tuple(run.objects.put(file.data) for file in reference.files)
        task_refs += (run.objects.put(task.run_policy), run.objects.put(task.judge_policy),
                      run.objects.put(published_judge_policy))
        retained_ref, retained_closure = write_document(run.objects, list(final_pool))
        reference_config = ({"development_reference": comparison.as_dict()}
                            if behavior_source != "provided" or reference_visible or provided_behavior is not None else {})
        configuration = run.objects.put(canonical_json({"experiment": experiment.as_dict(),
            "task": task.revision.as_dict(), "release": task.release.as_dict(), "native": native.identity,
            "technology": native.technology,
            "contract": task.contract.reference.content.as_dict(),
            "reference_behavior": task.reference_behavior.artifact.as_dict(),
            "run_policy": BlobRef.of(task.run_policy).as_dict(),
            "judge_policy": BlobRef.of(task.judge_policy).as_dict(),
            "final_pool_ref": retained_ref.as_dict(), "prompt": prompt, "attempt": spec.as_dict(),
            "sim_feedback": sim_feedback, "development_setting": setting, **reference_config}))
        tape.record("started", {"configuration": configuration.as_dict()}, (configuration, *retained_closure, *task_refs))
        archive = SearchArchive(run, task, experiment, native, comparison=comparison, sim_feedback=sim_feedback)
        # A supplied shell profile owns file operations too. The low-level
        # file-only profile remains usable by callers that supply no shell.
        tools = development_tools or file_tools()
        environment = ""
        if any(tool.name == "run_command" for tool in tools):
            environment = development_environment(setting)
        context = render("ppa/context", experiment=canonical_json(experiment.as_dict()).decode(),
                         technology=canonical_json(native.technology).decode())
        attempt = run_attempt(journal=run.journal("agent"), backend=backend, spec=spec,
            initial_files=allowed_files, tools=(*tools, *archive.tools()), workspace=workspace,
            initial_messages=role_messages("ppa", user_template="shared/task/user",
                development=development_instructions(setting), instructions=prompt,
                **task_variables(public, allowed_files, environment_docs="\n\n".join(
                    part for part in (context, comparison.description() if sim_feedback else "", environment) if part))),)
        tape.record("search_frozen", {"head": attempt.head.as_dict(), "candidates": sorted(archive.candidates)})
        final_pool_ref, pool_closure = write_document(run.objects, list(_union(archive.pool, final_pool)))
        if tape.begin("final_requested", {"pool": final_pool_ref.as_dict()}, pool_closure):
            archive.pool = _union(archive.pool, final_pool)
            billing = trajectory(run.journal("agent"))
            result = {"schema": "behave.ppa-search/2", "configuration": configuration.as_dict(),
                "reason": attempt.reason, "candidates": list(archive.candidates.values()), "pool_ref": final_pool_ref.as_dict(),
                "frontier": [], "evaluation_status": "awaiting_final", "billing": {
                    "cost_by_currency": thaw_json(billing.cost_by_currency()),
                    "unpriced_dispatches": billing.unpriced_dispatches,
                    "unresolved_dispatches": billing.unresolved_dispatches},
                "turns": len(billing.turns), "measurement_requests": archive.calls}
            ref, report_closure = write_document(run.objects, result)
            heads, closure = _seal_journals(run, "agent", "measurements")
            tape.record("finished", {"result": ref.as_dict(), "journals": heads}, (*report_closure, *closure))
        else:
            ref = BlobRef.from_dict(thaw_json(tape.take("finished").payload["result"]))
            result = read_document(run.objects, ref)
        tape.finish()
        return result
