"""Published-task CLI, workspace EDA diagnostics and read-only reporting."""

from types import SimpleNamespace

import pytest
from test_ppa import FakeNative, Scripted, experiment, fixture_task

from behave.agent.run_store import JournalDirectory
from behave.core import runtime_defaults as defaults
from behave.core.records import canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.data.published import ReleaseManifest, TaskManifest, TaskRevision
from behave.ppa.__main__ import main
from behave.ppa.report import summary, target_results
from behave.ppa.workflow import SearchArchive, eda_feedback


def published(tmp_path):
    public, task, _ = fixture_task()
    objects = DirectoryStore(tmp_path / "inputs")
    for f in (*public.files, *task.reference_behavior.files):
        objects.put(f.data)
    files = {f.reference.path: f.reference for f in public.files}
    manifest = TaskManifest(task.task_id, files["spec.md"], files["contract.json"],
        task.reference_behavior.artifact, objects.put(task.run_policy), objects.put(task.judge_policy))
    release = ReleaseManifest("ppa-cli-test", (TaskRevision(task.task_id, objects.put(manifest.to_bytes())),),
                              objects.put(b"fixture"))
    objects.put(release.to_bytes())
    (tmp_path / "release.json").write_bytes(release.to_bytes())
    (tmp_path / "experiment.json").write_bytes(canonical_json(experiment().as_dict()))
    (tmp_path / "provider.json").write_bytes(b"{}")
    (tmp_path / "prompt.txt").write_text("fixture-only draft")
    return ["--objects", str(objects.directory), "--release", str(tmp_path / "release.json"),
            "--task", task.task_id, "--experiment", str(tmp_path / "experiment.json"),
            "--liberty", "not-run.lib", "--sta", "not-run-sta"]


@pytest.mark.parametrize("release_pointer", [False, True])
def test_published_cli_search_compare_and_report(tmp_path, monkeypatch, capsys, release_pointer):
    import behave.agent.process_model as process
    from behave.agent.providers.local_chat import LocalChatEndpoint
    from behave.agent.tools import file_tools
    import behave.task_execution.shell as shell
    import behave.ppa.workflow as workflow
    from behave.task_execution.judge_policy import JudgePolicy

    # This test exercises CLI routing without opening a native namespace.
    # The real shell path has a separate native acceptance test.
    profiles, attempts = [], []

    def development(limits, *, setting, cpu_endpoint):
        profiles.append((limits, setting))
        return SimpleNamespace(tool=lambda: file_tools()[1])

    actual_search = workflow.run_search

    def recorded_search(**options):
        attempts.append(options["spec"])
        assert JudgePolicy.from_bytes(options["task"].judge_policy).search_seconds == 0
        return actual_search(**options)

    monkeypatch.setattr(shell, "development_shell", development)
    monkeypatch.setattr(workflow, "run_search", recorded_search)

    monkeypatch.setattr("behave.ppa.worker.NativePPA", lambda **kwargs: FakeNative())
    monkeypatch.setattr(process, "ProcessModelBackend", lambda command: Scripted())
    monkeypatch.setattr(LocalChatEndpoint, "from_dict", lambda row: SimpleNamespace(
        model_policy=lambda: {"model": "fake"}, command=lambda: "no-process"))
    common = published(tmp_path)
    if release_pointer:
        from behave.core.artifacts import BlobRef

        path = tmp_path / "release.json"
        path.write_bytes(canonical_json(BlobRef.of(path.read_bytes()).as_dict()))
    args = [*common, "--provider", str(tmp_path / "provider.json"), "--prompt", str(tmp_path / "prompt.txt"),
            "--output", str(tmp_path / "search")]
    assert main(args) == 0  # Original pilot's flag-only form.
    first = parse_json(capsys.readouterr().out.encode())
    assert first["methods"]["search"]["candidates"] == 1
    assert first["evaluation_status"] == "awaiting_final"
    assert first["methods"]["search"]["targets"][0]["time_ns"] is None
    assert first["cost"]["search_service_calls"] == 3
    assert first["cost"]["final_service_calls"] == 0
    assert first["cost"]["reason"] == "final_action"
    assert attempts[0].max_turns == defaults.AGENT_MAX_TURNS
    assert attempts[0].request_timeout_seconds is None
    assert profiles[0][1] == "rtl_sim"
    assert main(["search", *args]) == 0
    assert parse_json(capsys.readouterr().out.encode()) == first
    # Different replicates may use one monetary account. Their model call IDs
    # must not alias, while each replicate's cold replay above is unchanged.
    other = args[:-1] + [str(tmp_path / "another-search")]
    assert main(["search", *other]) == 0
    capsys.readouterr()
    first_call = next(e.payload["call_id"] for e in JournalDirectory(tmp_path / "search").journal("agent").load()
                      if e.kind == "model_requested")
    other_call = next(e.payload["call_id"] for e in JournalDirectory(tmp_path / "another-search").journal("agent").load()
                      if e.kind == "model_requested")
    assert first_call != other_call
    compare = ["compare", *common, "--search", "one", str(tmp_path / "search"),
               "--search", "two", str(tmp_path / "search"), "--output", str(tmp_path / "final")]
    assert main(compare) == 0
    result = parse_json(capsys.readouterr().out.encode())
    assert result["methods"]["one"] == result["methods"]["two"]
    assert result["cost"]["final_service_calls"] == 1  # Same bytes, distinct memberships.
    assert result["cost"]["searches"]["one"]["billing"] == first["cost"]["billing"]
    assert result["cost"]["searches"]["two"]["search_service_calls"] == 3
    assert main(compare) == 0
    assert parse_json(capsys.readouterr().out.encode()) == result

    def forbidden(**kwargs):
        raise AssertionError("report must not initialize EDA")

    monkeypatch.setattr("behave.ppa.worker.NativePPA", forbidden)
    assert main(["report", str(tmp_path / "final")]) == 0
    assert parse_json(capsys.readouterr().out.encode()) == result


def test_duplicate_cli_labels_are_not_silently_overwritten(tmp_path, monkeypatch):

    monkeypatch.setattr("behave.ppa.worker.NativePPA", lambda **kwargs: FakeNative())
    with pytest.raises(SystemExit) as error:
        main(["compare", *published(tmp_path), "--search", "one", "a", "--search", "one", "b",
              "--output", str(tmp_path / "final")])
    assert error.value.code == 2


def test_compare_cli_assigns_a_distinct_remote_ingress_to_each_final_worker(tmp_path, monkeypatch, capsys):
    import behave.ppa.finalize as finalize

    seen = []
    monkeypatch.setattr("behave.ppa.worker.NativePPA", lambda **kwargs: FakeNative())
    monkeypatch.setattr("behave.ppa.report.summary", lambda _: {})

    def compare(**options):
        seen.extend(worker.cpu_resources for worker in options['workers'])
        assert len({id(worker) for worker in options['workers']}) == 2

    monkeypatch.setattr(finalize, 'compare_searches', compare)
    assert main(['compare', *published(tmp_path), '--search', 'one', 'search',
                 '--output', str(tmp_path / 'final'), '--cpu-endpoint', '/first',
                 '--cpu-endpoint', '/second']) == 0
    assert seen == ['/first', '/second']


def test_cli_rejects_reusing_one_remote_ingress_for_concurrent_workers(tmp_path, monkeypatch):

    monkeypatch.setattr("behave.ppa.worker.NativePPA", lambda **kwargs: FakeNative())
    with pytest.raises(SystemExit):
        main(['compare', *published(tmp_path), '--search', 'one', 'search',
              '--output', str(tmp_path / 'final'), '--cpu-endpoint', '/same', '--cpu-endpoint', '/same'])


def test_report_requires_durable_completion(tmp_path):
    with pytest.raises(ValueError, match="durably completed"):
        summary(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_bad_model_candidate_ids_are_repairable_not_infrastructure_errors(tmp_path):
    from behave.agent.attempt import AttemptSpec
    from behave.agent.model import ModelReply, ToolCall
    from behave.ppa.workflow import run_search

    bad = [("measure_ppa", {"candidate": []}), ("measure_ppa", {"candidate": {}})]

    class Repair(Scripted):
        calls = 0

        def generate(self, request):
            turn = self.calls
            self.calls += 1
            if 0 < turn <= len(bad):
                assert request.messages[-2]["content"]["error"] == "record-text-invalid"
            if turn < len(bad):
                name, args = bad[turn]
                return ModelReply("", (ToolCall("bad" + str(turn), name, args),))
            return super().generate(request)

    public, task, _ = fixture_task()
    result = run_search(public=public, task=task, experiment=experiment(),
        spec=AttemptSpec("repair", "ppa", {"model": "fake"}), backend=Repair(), native=FakeNative(),
        directory=tmp_path, prompt="fixture draft")
    assert len(result["candidates"]) == 1 and result["turns"] == 6


def test_target_report_keeps_no_solution_and_uses_measurements_not_target_labels():
    def candidate(key, area, time, **kwargs):
        return {"candidate": key, "score": 1, "mapped_score": 1,
                "eda": {"status": "measured", "area_um2": area, "timing": {"status": "met"}},
                "performance": {"score": 1, "time_ns": time}, "target": 0, **kwargs}

    rows = [candidate("small", 10., 100.), candidate("fast", 12., 80.),
            candidate("wrong", 1., 1., score=0), candidate("unknown", 1., 1., mapped_score=None)]
    assert [r["candidate"] for r in target_results(rows, (70., 80., 100.))] == [None, "fast", "small"]
    assert target_results((), (100.,)) == [
        {"bound_ns": 100., "candidate": None, "area_um2": None, "time_ns": None, "power": None}]


@pytest.mark.parametrize("status", ["measured", "synthesis-failed"])
def test_eda_feedback_keeps_full_workspace_log_and_public_diagnostics(tmp_path, status):
    from behave.agent.local_workspace import LocalWorkspace
    from behave.ppa.records import pack_result, read_log

    _, task, _ = fixture_task()
    archive = SearchArchive(JournalDirectory(tmp_path), task, experiment(), FakeNative())
    workspace = LocalWorkspace(archive.run.objects, tmp_path / "live")
    log = "header\n" + "\u4e2d" * 20000 + "\nimportant tail"
    eda = {"stage": "synthesis", "status": status, "area_um2": 1., "timing": {}, "log": log,
           "simulation_source": {"PRIVATE": "source"}, "evidence": {"PRIVATE": "handle"}}
    if status != "measured":
        eda.update(area_um2=None, failed_stage="synthesis",
                   stages={"synthesis": {"status": "failed", "reason": "nonzero-exit", "returncode": 1}})
    public, files = eda_feedback(eda, prefix="eda/first")
    for file in files:
        workspace.write(file.reference.path, file.data)
    assert "PRIVATE" not in repr((public, files))
    assert "header" not in repr(public) and "log_chars" not in public
    assert workspace.read(public["log"]).decode() == log
    report = workspace.read(public["report"]).decode()
    assert f"Status: {status}" in report and "eda.log" in report
    if status != "measured":
        assert public["failed_stage"] == "synthesis" and "nonzero-exit" in report
    assert "read_eda_log" not in {tool.name for tool in archive.tools()}

    # The same exporter reads retained logs after a cold archive load.
    packed, _ = pack_result(archive.run.objects, {"eda": {k: v for k, v in eda.items() if k != "evidence"}})
    replay, restored = eda_feedback(packed["eda"], archive.run.objects, prefix="eda/first")
    assert (replay, restored) == (public, files)
    assert read_log(archive.run.objects, packed["eda"]) == log
    _, later = eda_feedback({**eda, "log": "new measurement"}, prefix="eda/second")
    for file in later:
        workspace.write(file.reference.path, file.data)
    assert workspace.read(public["log"]).decode() == log
    assert archive.calls == 0 and archive.events == ()


def test_absent_eda_has_no_invented_report():
    assert eda_feedback(None, prefix="eda/absent") == (None, ())


def test_synthesis_timeout_feedback_includes_reason_and_wall_budget():
    eda = {'status': 'timeout', 'area_um2': None, 'timing': None, 'log': 'full tool log',
           'failed_stage': 'synthesis', 'stages': {'synthesis': {
               'status': 'timeout', 'reason': 'tool-deadline', 'budget_seconds': 1800,
               'elapsed_seconds': 1800.5, 'internal': 'PRIVATE'}}}
    public, files = eda_feedback(eda, prefix='eda/timeout')
    assert public['failure'] == {'reason': 'tool-deadline', 'budget_seconds': 1800,
                                 'elapsed_seconds': 1800.5}
    assert 'PRIVATE' not in repr(public)
    assert 'Stage wall-time limit: 1800 s' in files[0].data.decode()
    assert 'Stage elapsed wall time: 1800.5 s' in files[0].data.decode()
    assert files[1].data == b'full tool log'


def test_eda_service_error_survives_feedback_and_cold_archive(tmp_path):
    from test_ppa_review import archive_with_candidate

    archive, key = archive_with_candidate(tmp_path)
    archive.action(key, 'eda')
    archive.record(key, 'eda', {'score': None, 'code': 'worker-timeout'}, 1200.)
    archive.snapshot()
    restored = SearchArchive(archive.run, archive.task, archive.experiment, FakeNative())
    eda = restored.candidates[key]['eda']
    assert eda['status'] == 'no-result' and eda['area_um2'] is None
    public, files = eda_feedback(eda, archive.run.objects, prefix='eda/no-result')
    assert public['code'] == 'worker-timeout'
    assert 'failed_stage' not in public  # The outer timeout does not identify a synthesis stage.
    assert 'Reason: worker-timeout' in files[0].data.decode()
