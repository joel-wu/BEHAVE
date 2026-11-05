"""Public-source acquisition tests; fixtures never reach the public internet."""

import http.client
import io
import socket
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from behave.agent.attempt import AttemptInterrupted
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.lifecycle import CleanupState
from behave.core.records import RecordError, canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.data.drafts import ScopeProposal, SourceRecord, load_scope, scope_closure, source_closure
from behave.data.ingest import (
    FetchPlan,
    FetchPolicy,
    RemoteFile,
    acquire_source,
    fetch_http,
    fetch_worker,
    verify_acquisition,
)
from behave.data.ingest.fetch_evidence import trace_result
from behave.data.ingest.fetch_records import public_url
from behave.isolation.service import ServiceResult

URL = "https://sources.example/model.c"
PEER = "8.8.8.8"
SOURCE = b"int f(int a) { return a ^ 7; }\n"


def selection(**kwargs):
    return FetchPlan("xor", "https://sources.example/project", "revision-declared", "MIT",
                     (RemoteFile("model.c", URL, **kwargs),), "model.c", "f")


def downloaded(body=SOURCE):
    return fetch_http.Hop("downloaded", PEER, 200, body=body)


def fake_service(monkeypatch, replies, *, result=None):
    """Replace only process launch and HTTP; keep worker, journal and verifier real."""
    calls = []

    def get(url, *, maximum, deadline):
        calls.append(url)
        assert deadline > time.monotonic()
        hop = replies[url]
        if hop.body is not None and len(hop.body) > maximum:
            return fetch_http.Hop("fetch-byte-limit", PEER, 200)
        return hop

    def service(argv, **options):
        assert options["network"] == "host"
        assert set(options["environment"]) == {"PATH", "LANG", "TMPDIR", "PYTHONDONTWRITEBYTECODE"}
        assert argv[1:3] == ("-I", "-B")
        root, digest, size, policy_digest, policy_size = argv[-5:]
        assert options["environment"]["TMPDIR"] == root
        local = JournalDirectory(root)
        plan = FetchPlan.from_bytes(local.objects.read(BlobRef(digest, int(size))))
        policy = FetchPolicy.from_bytes(local.objects.read(BlobRef(policy_digest, int(policy_size))))
        fetch_worker.execute(plan, policy, local.journal("trace"))
        return result or ServiceResult("exited", 0, b"", b"", .01)

    monkeypatch.setattr(fetch_http, "get", get)
    monkeypatch.setattr("behave.isolation.service.run_service", service)
    return calls


@pytest.mark.parametrize("url", ["http://sources.example/x", "file:///etc/passwd", "https://user:secret@sources.example/x",
    "https://sources.example:443/x", "https://sources.example/x?token=secret", "https://sources.example/x?",
    "https://sources.example/x#part", "https://sources.example/x#", "https://sources.example./x",
    "https://[::1]/x", "https://127.0.0.1/x", "https://sources.example/\nx", "https://sources.example\\evil/x",
    "https://sources.example/%xx", "https://SOURCES.example/x", "https://sources.example/中文"])
def test_fetch_urls_are_closed_public_requests(url):
    with pytest.raises(RecordError):
        public_url(url)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "0.0.0.0", "100.64.0.1",
    "192.168.1.1", "224.0.0.1", "240.0.0.1", "::1", "fe80::1", "fc00::1", "ff02::1", "::ffff:127.0.0.1",
    "64:ff9b::7f00:1", "2002:7f00:1::", "2001::1"])
def test_fetch_rejects_private_and_transition_addresses(monkeypatch, address):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    target = (address, 443, 0, 0) if family == socket.AF_INET6 else (address, 443)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (PEER, 443)),
        (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", target)])
    assert not fetch_http.public_address(address)
    with pytest.raises(RecordError, match="public unicast"):
        fetch_http.resolve("sources.example")


def test_fetch_connect_pins_address_but_verifies_original_tls_hostname(monkeypatch):
    calls = []

    class Socket:
        def settimeout(self, timeout):
            assert 0 < timeout <= 10

        def connect(self, address):
            calls.append(("connect", address))

        def close(self):
            calls.append(("close",))

    def wrap(sock, *, server_hostname):
        calls.append(("tls", server_hostname))
        return sock

    monkeypatch.setattr(socket, "socket", lambda *a: Socket())
    monkeypatch.setattr(fetch_http.ssl, "create_default_context", lambda: SimpleNamespace(wrap_socket=wrap))
    monkeypatch.setattr(http.client, "HTTPSConnection", lambda *a, **k: SimpleNamespace(sock=None))
    result = fetch_http.connect("sources.example", socket.AF_INET, (PEER, 443), time.monotonic() + 10)
    assert result.sock is not None
    assert calls == [("connect", (PEER, 443)), ("tls", "sources.example")]


def wire(monkeypatch, response_bytes):
    class Socket:
        def makefile(self, *args):
            return io.BytesIO(response_bytes)

        def settimeout(self, timeout):
            assert timeout > 0

    class Connection:
        sock = Socket()
        closed = False

        def request(self, method, path, *, headers):
            assert method == "GET" and path == "/model.c"
            assert headers == {"Accept-Encoding": "identity", "User-Agent": "behave-source-fetch/1", "Connection": "close"}

        def getresponse(self):
            response = http.client.HTTPResponse(self.sock)
            response.begin()
            return response

        def close(self):
            self.closed = True

    connection = Connection()
    queries = []

    def dns(host, port, **kwargs):
        queries.append((host, port))
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (PEER, 443))]

    monkeypatch.setattr(socket, "getaddrinfo", dns)
    monkeypatch.setattr(fetch_http, "connect", lambda *args: connection)
    hop = fetch_http.get(URL, maximum=4, deadline=time.monotonic() + 10)
    assert connection.closed and queries == [("sources.example", 443)]
    return hop


@pytest.mark.parametrize(("headers", "body", "code"), [
    (b"Content-Length: 4\r\n", b"abcd", "downloaded"),
    (b"Content-Length: 4\r\n", b"abc", "fetch-body-incomplete"),
    (b"Content-Length: 5\r\n", b"abcde", "fetch-byte-limit"),
    (b"", b"abcde", "fetch-byte-limit"),
    (b"Transfer-Encoding: chunked\r\n", b"4\r\nabcd\r\n0\r\n\r\n", "downloaded"),
    (b"Transfer-Encoding: chunked\r\n", b"4\r\nabcd\r\n", "fetch-body-incomplete"),
    (b"Transfer-Encoding: chunked\r\nContent-Length: 4\r\n", b"abcd", "fetch-headers-invalid"),
    (b"Transfer-Encoding: gzip\r\n", b"abcd", "fetch-headers-invalid"),
    (b"Content-Encoding: gzip\r\n", b"abcd", "fetch-content-encoding"),
    (b"Content-Length: 4\r\nContent-Length: 4\r\n", b"abcd", "fetch-headers-invalid"),
    (b"Content-Range: bytes 0-3/10\r\n", b"abcd", "fetch-headers-invalid"),
])
def test_fetch_uses_complete_bounded_shared_http_framing(monkeypatch, headers, body, code):
    hop = wire(monkeypatch, b"HTTP/1.1 200 OK\r\n" + headers + b"\r\n" + body)
    assert hop.code == code
    assert hop.body == (b"abcd" if code == "downloaded" else None)


def test_fetch_http_rejection_does_not_read_body_or_forward_cookies(monkeypatch):
    hop = wire(monkeypatch, b"HTTP/1.1 404 Missing\r\nContent-Length: 999999999\r\nSet-Cookie: secret\r\n\r\n")
    assert hop.code == "fetch-http-status" and hop.status == 404
    assert dict(hop.headers) == {"content-length": "999999999"}


@pytest.mark.parametrize("status", [408, 429, 500, 503])
def test_fetch_transient_http_failure_is_not_a_bad_source(monkeypatch, tmp_path, status):
    hop = wire(monkeypatch, f"HTTP/1.1 {status} Unavailable\r\nContent-Length: 0\r\n\r\n".encode())
    assert hop.code == "fetch-http-unavailable"
    fake_service(monkeypatch, {URL: hop})
    result = acquire_source(selection(), FetchPolicy(("sources.example",)), run=JournalDirectory(tmp_path / "run"))
    assert (result.status, result.code) == ("unavailable", "fetch-http-unavailable")


def test_fetch_success_exports_to_existing_scope_and_replays_offline(tmp_path, monkeypatch):
    plan = replace(selection(expected=BlobRef.of(SOURCE)), files=(RemoteFile("LICENSE", "https://sources.example/LICENSE"),
        *selection(expected=BlobRef.of(SOURCE)).files))
    calls = fake_service(monkeypatch, {URL: fetch_http.Hop("redirect", PEER, 302, location="/pinned/model.c"),
        "https://sources.example/pinned/model.c": downloaded(), "https://sources.example/LICENSE": downloaded(b"MIT")})
    monkeypatch.setenv("HTTPS_PROXY", "http://secret:secret@127.0.0.1:1234")
    run = JournalDirectory(tmp_path / "run")
    result = acquire_source(plan, FetchPolicy(("sources.example",)), run=run)
    assert (result.status, result.code, result.cleanup) == ("acquired", "complete", CleanupState.COMPLETE)
    scope = run.objects.put(ScopeProposal(result.source, "retain XOR", ("XOR",), ("eight-bit",)).to_bytes())
    _, source, files = load_scope(run.objects, scope)
    assert (source.source_id, source.revision, source.license) == ("xor", "revision-declared", "MIT")
    assert {f.reference.path: f.data for f in files} == {"LICENSE": b"MIT", "model.c": SOURCE}
    assert source.acquisition is not None and SourceRecord.from_bytes(source.to_bytes()) == source
    assert calls == ["https://sources.example/LICENSE", URL, "https://sources.example/pinned/model.c"]
    monkeypatch.setattr("behave.isolation.service.run_service", lambda *a, **k: pytest.fail("cold replay dispatched"))
    assert acquire_source(plan, FetchPolicy(("sources.example",)), run=run) == result
    _, closure = run.seal("acquisition")
    copied = DirectoryStore(tmp_path / "export")
    copied.publish(run.objects, closure)
    assert verify_acquisition(copied, result.head) == result
    from behave.cli.inspect import inspect_record

    assert inspect_record("acquisition", copied, result.head) == result.as_dict()
    (copied.directory / BlobRef.of(SOURCE).sha256).unlink()
    with pytest.raises(RecordError):
        verify_acquisition(copied, result.head)


def test_fetch_provenance_is_part_of_source_and_scope_export(tmp_path, monkeypatch):
    fake_service(monkeypatch, {URL: downloaded()})
    run = JournalDirectory(tmp_path / "run")
    result = acquire_source(selection(), FetchPolicy(("sources.example",)), run=run)
    source = SourceRecord.from_bytes(run.objects.read(result.source))
    scope = run.objects.put(ScopeProposal(result.source, "XOR", ("XOR",), ("8 bits",)).to_bytes())
    copied = DirectoryStore(tmp_path / "scope-export")
    copied.publish(run.objects, scope_closure(run.objects, scope))
    assert load_scope(copied, scope)[1] == source
    assert source.acquisition in source_closure(copied, result.source)
    for change in ({"license": "not-verified"}, {"revision": "different"},
                   {"selected": replace(source.selected, entry_symbol="different")}):
        modified = copied.put(replace(source, **change).to_bytes())
        with pytest.raises(RecordError, match="fixed plan"):
            source_closure(copied, modified)
    (copied.directory / source.acquisition.sha256).unlink()
    with pytest.raises(RecordError):
        load_scope(copied, scope)


@pytest.mark.isolation
def test_fetch_source_enters_real_scope_role_with_full_provenance(tmp_path, monkeypatch):
    from test_scope_preparation import prepared

    from behave.data.scope import prepare_scope

    options, _, _, scope_options = prepared(tmp_path, revise=False)
    store = options["reader"]
    author = scope_options["plan"].author
    original = SourceRecord.from_bytes(store.read(author.inputs.source))
    chosen = original.selected
    fetch_plan = FetchPlan(original.source_id, "https://sources.example/project", "fixture", None,
        tuple(RemoteFile(f.path, "https://sources.example/" + f.path, f.content) for f in chosen.files),
        chosen.entry_file, chosen.entry_symbol)
    acquired = JournalDirectory(tmp_path / "acquired")
    with monkeypatch.context() as patch:
        fake_service(patch, {"https://sources.example/" + f.path: downloaded(store.read(f.content)) for f in chosen.files})
        fetched = acquire_source(fetch_plan, FetchPolicy(("sources.example",)), run=acquired)
    store.publish(acquired.objects, source_closure(acquired.objects, fetched.source))
    scope_options["plan"] = replace(scope_options["plan"], author=replace(author,
        inputs=replace(author.inputs, source=fetched.source)))
    result = prepare_scope(**scope_options)
    assert result.status == "review_ready"
    exported = DirectoryStore(tmp_path / "scope-output")
    exported.publish(scope_options["run"].objects, scope_closure(scope_options["run"].objects, result.scopes[-1]))
    _, source, _ = load_scope(exported, result.scopes[-1])
    assert source.acquisition is not None
    assert exported.read(source.acquisition) == acquired.objects.read(source.acquisition)


@pytest.mark.parametrize(("hop", "policy", "expected"), [
    (fetch_http.Hop("redirect", PEER, 302, location="http://sources.example/x"), {}, "fetch-redirect-invalid"),
    (fetch_http.Hop("redirect", PEER, 302, location="https://user:secret@sources.example/x"), {}, "fetch-redirect-invalid"),
    (fetch_http.Hop("redirect", PEER, 302, location="https://other.example/x"), {}, "fetch-host-forbidden"),
    (fetch_http.Hop("redirect", PEER, 302, location="/model.c"), {}, "fetch-redirect-loop"),
    (fetch_http.Hop("redirect", PEER, 302, location="/next"), {"max_redirects": 0}, "fetch-redirect-limit"),
    (fetch_http.Hop("redirect", PEER, 302), {}, "fetch-redirect-invalid"),
    (fetch_http.Hop("fetch-transport-failed"), {}, "fetch-transport-failed"),
])
def test_fetch_rejections_are_evidence_not_source_or_fallback(tmp_path, monkeypatch, hop, policy, expected):
    calls = fake_service(monkeypatch, {URL: hop})
    run = JournalDirectory(tmp_path / "run")
    result = acquire_source(selection(), FetchPolicy(("sources.example",), **policy), run=run)
    assert result.source is None and result.code == expected and calls == [URL]
    assert result.status == ("unavailable" if expected == "fetch-transport-failed" else "rejected")
    assert verify_acquisition(run.objects, result.head) == result
    # A rejected credential-bearing redirect is represented by a digest, not plaintext.
    for path in run.objects.directory.iterdir():
        assert b"user:secret" not in path.read_bytes()


@pytest.mark.parametrize("kind", ["expected", "file-limit", "total-limit"])
def test_fetch_changed_or_oversized_bytes_never_become_a_source(tmp_path, monkeypatch, kind):
    plan = selection(expected=BlobRef.of(b"wrong")) if kind == "expected" else selection()
    policy = FetchPolicy(("sources.example",), **({"file_bytes": 1} if kind == "file-limit" else
                                               {"total_bytes": 1} if kind == "total-limit" else {}))
    fake_service(monkeypatch, {URL: downloaded()})
    result = acquire_source(plan, policy, run=JournalDirectory(tmp_path / "run"))
    assert result.source is None and result.status == "rejected"
    assert result.code == ("fetch-content-mismatch" if kind == "expected" else "fetch-byte-limit")


def test_fetch_pending_owner_is_not_reexecuted_and_changed_plan_is_refused(tmp_path, monkeypatch):
    from behave.isolation import service

    calls = fake_service(monkeypatch, {URL: downloaded()})
    run = JournalDirectory(tmp_path / "run")
    original = service.run_service

    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise SystemExit('coordinator lost before service completion was recorded')

    with monkeypatch.context() as fault:
        fault.setattr(service, 'run_service', interrupted)
        with pytest.raises(SystemExit):
            acquire_source(selection(), FetchPolicy(("sources.example",)), run=run)
    with pytest.raises(AttemptInterrupted) as error:
        acquire_source(selection(), FetchPolicy(("sources.example",)), run=run)
    assert error.value.code == "fetch-dispatch-pending" and calls == [URL]
    with pytest.raises(RecordError) as error:
        acquire_source(replace(selection(), entry_symbol="g"), FetchPolicy(("sources.example",)), run=run)
    assert error.value.code == "fetch-run-mismatch"


@pytest.mark.parametrize("status", ["timeout", "cancelled", "output-limit", "cleanup-incomplete"])
def test_fetch_source_requires_successful_service_and_cleanup(tmp_path, monkeypatch, status):
    fake_service(monkeypatch, {URL: downloaded()}, result=ServiceResult(status, -9, b"", b"", .01))
    # Unknown-cleanup staging must remain available to the external lifetime owner.
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv('BEHAVE_SCRATCH_DIR', str(scratch))
    result = acquire_source(selection(), FetchPolicy(("sources.example",)), run=JournalDirectory(tmp_path / "run"))
    assert result.source is None and result.status == "unavailable"
    assert bool(list(scratch.iterdir())) == (status == "cleanup-incomplete")


def test_fetch_trace_does_not_accept_partial_or_reordered_files(tmp_path, monkeypatch):
    fake_service(monkeypatch, {URL: downloaded()})
    plan, policy = selection(), FetchPolicy(("sources.example",))
    run = JournalDirectory(tmp_path / "trace")
    fetch_worker.execute(plan, policy, run.journal("trace"))
    events = run.journal("trace").load()
    prefix = run.objects.put(events[1].to_bytes())
    run.objects.put(events[0].to_bytes())
    assert trace_result(run.objects, prefix, plan, policy) == (None, None)
    wrong = replace(events[0], payload={"file": 1, "url": URL})
    with pytest.raises(RecordError):
        trace_result(run.objects, run.objects.put(wrong.to_bytes()), plan, policy)


def test_fetch_cli_validation_has_no_network_or_store_side_effect(tmp_path, monkeypatch, capsys):
    from behave.data.__main__ import main
    from behave.data.ingest import fetch_cli

    plan, policy = tmp_path / "plan.json", tmp_path / "policy.json"
    plan.write_bytes(selection().to_bytes() + b"\n")
    policy.write_bytes(FetchPolicy(("sources.example",)).to_bytes())
    run = tmp_path / "run"
    args = ["fetch", "--plan", str(plan), "--policy", str(policy), "--run-directory", str(run)]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: pytest.fail("dry-run used DNS"))
    with monkeypatch.context() as local:
        local.setattr(fetch_cli, "command_scope", lambda **kw: pytest.fail("dry-run installed process controls"))
        assert main([*args, "--dry-run"]) == 0
    assert parse_json(capsys.readouterr().out.encode())["status"] == "validated" and not run.exists()
    fake_service(monkeypatch, {URL: downloaded()})
    assert main(args) == 0
    assert parse_json(capsys.readouterr().out.encode())["status"] == "acquired"


def test_fetch_cancellation_before_dispatch_has_no_http_request(tmp_path, monkeypatch):
    def service(argv, **options):
        assert options["cancelled"]()
        return ServiceResult("cancelled", None, b"", b"", 0.)

    monkeypatch.setattr("behave.isolation.service.run_service", service)
    result = acquire_source(selection(), FetchPolicy(("sources.example",)), run=JournalDirectory(tmp_path / "run"),
                            cancelled=lambda: True)
    assert (result.status, result.code, result.source) == ("unavailable", "fetch-worker-cancelled", None)


@pytest.mark.isolation
def test_fetch_real_service_denies_host_before_network_and_replays(tmp_path):
    plan, policy = selection(), FetchPolicy(("different.example",))
    run = JournalDirectory(tmp_path / "run")
    result = acquire_source(plan, policy, run=run)
    assert (result.status, result.code, result.source) == ("rejected", "fetch-host-forbidden", None)
    assert result.cleanup == CleanupState.COMPLETE
    assert acquire_source(plan, policy, run=run) == result


@pytest.mark.parametrize("change", [{"timeout_seconds": True}, {"max_redirects": -1}, {"max_files": 0},
                                    {"file_bytes": 0}, {"total_bytes": 0}])
def test_fetch_policy_rejects_invalid_budgets(change):
    with pytest.raises(ValueError):
        FetchPolicy(("sources.example",), **change)


def test_fetch_policy_uses_operator_budgets_without_second_ceilings():
    policy = FetchPolicy(("sources.example",), timeout_seconds=601, max_redirects=11, max_files=257,
                         file_bytes=17 << 20, total_bytes=65 << 20)
    assert FetchPolicy.from_bytes(policy.to_bytes()) == policy


def test_fetch_canonical_records_and_policy_caps():
    plan, policy = selection(), FetchPolicy(("sources.example",))
    assert FetchPlan.from_bytes(plan.to_bytes()) == plan
    assert FetchPolicy.from_bytes(policy.to_bytes()) == policy
    with pytest.raises(RecordError):
        FetchPlan.from_bytes(plan.to_bytes() + b"\n")
    with pytest.raises(RecordError):
        FetchPolicy.from_bytes(canonical_json({**parse_json(policy.to_bytes()), "proxy": "ambient"}))
    with pytest.raises(RecordError):
        replace(policy, file_bytes=1).check(selection(expected=BlobRef.of(SOURCE)))
    with pytest.raises(RecordError):
        replace(plan, files=(*plan.files, plan.files[0]))
