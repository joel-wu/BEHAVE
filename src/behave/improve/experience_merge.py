"""Short, bounded consolidation batches using the existing role supervisor.

Each accepted batch is committed before the next call. Ordinary failure keeps
the last accepted version and an explicit backlog; owner cancellation pauses.
"""

from dataclasses import replace

from behave.agent.journal import load_chain
from behave.agent.lifetime import AttemptControl
from behave.agent.prompting import render
from behave.agent.replay import Tape
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef, read_verified
from behave.core.records import RecordError, thaw_json

from .acquisition import AcquisitionConfiguration, acquire_materials, load_acquisition_report
from .experience import (
    ExperiencePolicy,
    delta_batches,
    delta_files,
    experience_files,
    experience_snapshot,
    load_experience,
    memory_value,
    views,
)
from .memory_recovery import memory_child


def batch_configuration(configuration, prior, refs, ordinal):
    policy = ExperiencePolicy.from_dict(configuration.job.inputs.experience_policy)
    from .experience import memory_job
    job = memory_job(configuration.job, policy)
    return replace(configuration, job=replace(job,
        inputs=replace(job.inputs, experience_mode=f"compact-v{policy.version}", experience=prior,
                       experience_proposals=refs, pending_experience=()),
        attempt=replace(job.attempt, attempt_id=f"{job.attempt.attempt_id}:batch:{ordinal}",
            max_turns=policy.max_turns, max_retries=0, request_timeout_seconds=policy.request_seconds,
            context_tokens=policy.context_tokens, context_reset_message=None)))


def _result(prior, pending, status):
    return {"schema": "behave.experience-merge/1", "status": status,
            "materials": None if prior is None else {"experience": prior.as_dict()},
            "pending_experience": [ref.as_dict() for ref in pending]}


def consolidate_memory(*, configuration, reader, run, control, model_pool=None):
    from behave.data.role import input_closure

    from .acquisition import acquire_materials
    from .memory_recovery import memory_child

    with run.owner():
        objects, tape = run.objects, Tape(run.journal("acquisition"))
        config_ref = objects.put(configuration.to_bytes())
        if not tape.events:
            refs = input_closure(configuration.job, reader)
            objects.publish(reader, refs)
            tape.record("memory_started", {"configuration": config_ref.as_dict()}, (config_ref, *refs))
        else:
            tape.record("memory_started", {"configuration": config_ref.as_dict()})
        if tape.events[-1].kind == "memory_finished":
            head, _ = run.seal("acquisition")
            return load_memory_report(objects, head)
        inputs = configuration.job.inputs
        policy = ExperiencePolicy.from_dict(inputs.experience_policy)
        batches = delta_batches(objects, inputs.experience, inputs.experience_proposals, policy)
        prior, pending = inputs.experience, ()
        for ordinal, refs in enumerate(batches):
            configured = batch_configuration(configuration, prior, refs, ordinal)
            if tape.cursor < len(tape.events):
                event = tape.take("memory_batch")
                row = thaw_json(event.payload)
                if row["configuration"] != BlobRef.of(configured.to_bytes()).as_dict():
                    raise RecordError("memory-replay-context", "saved consolidation batch changed")
                outcome = row["outcome"]
            else:
                if control.cancelled():
                    return _result(prior, tuple(ref for batch in batches[ordinal:] for ref in batch), "interrupted")
                child = memory_child(run.directory / "batches" / f"{ordinal:04d}", configured)
                outcome = acquire_materials(configuration=configured, reader=objects, run=child,
                                            control=control, model_pool=model_pool)
                if outcome["status"] == "interrupted" and control.cancelled():
                    return _result(prior, tuple(ref for batch in batches[ordinal:] for ref in batch), "interrupted")
                head, closure = child.seal("acquisition")
                objects.publish_owned(child.objects, closure)
                ref = objects.put(configured.to_bytes())
                tape.record("memory_batch", {"configuration": ref.as_dict(), "head": head.as_dict(),
                            "outcome": outcome}, (ref, *closure))
            if outcome["status"] != "completed":
                pending = tuple(ref for batch in batches[ordinal:] for ref in batch)
                break
            prior = BlobRef.from_dict(outcome["materials"]["experience"])
        result = _result(prior, pending, "unfinished" if pending else "completed")
        tape.record("memory_finished", result)
        tape.finish()
        head, _ = run.seal("acquisition")
        return load_memory_report(objects, head)


def load_memory_report(reader, head):
    from .acquisition import AcquisitionConfiguration, load_acquisition_report

    events = load_chain(reader, head)
    if (not events or events[0].kind != "memory_started" or events[-1].kind != "memory_finished"
            or any(e.kind != "memory_batch" for e in events[1:-1])):
        raise RecordError("memory-incomplete", "consolidation has not settled")
    configuration = AcquisitionConfiguration.from_bytes(read_verified(reader,
        BlobRef.from_dict(thaw_json(events[0].payload)["configuration"])))
    inputs = configuration.job.inputs
    if inputs.experience_mode != "merge-v1":
        raise RecordError("memory-mode", "unexpected consolidation mode")
    policy = ExperiencePolicy.from_dict(inputs.experience_policy)
    batches = delta_batches(reader, inputs.experience, inputs.experience_proposals, policy)
    prior, pending = inputs.experience, ()
    children = events[1:-1]
    for ordinal, event in enumerate(children):
        if ordinal >= len(batches) or pending:
            raise RecordError("memory-batch-order", "unexpected consolidation batch")
        configured = batch_configuration(configuration, prior, batches[ordinal], ordinal)
        row = thaw_json(event.payload)
        child = load_chain(reader, BlobRef.from_dict(row["head"]))
        ref = BlobRef.of(configured.to_bytes()).as_dict()
        if row["configuration"] != ref or child[0].payload["configuration"] != ref:
            raise RecordError("memory-batch-context", "consolidation changed its inputs or budgets")
        outcome = load_acquisition_report(reader, BlobRef.from_dict(row["head"]))
        if outcome != row["outcome"]:
            raise RecordError("memory-batch-outcome", "consolidation changed its result")
        if outcome["status"] == "completed":
            prior = BlobRef.from_dict(outcome["materials"]["experience"])
        else:
            pending = tuple(ref for batch in batches[ordinal:] for ref in batch)
    if not pending and len(children) != len(batches):
        raise RecordError("memory-batch-missing", "unprocessed experience was not retained")
    result = _result(prior, pending, "unfinished" if pending else "completed")
    if thaw_json(events[-1].payload) != result:
        raise RecordError("memory-result", "memory or backlog changed after settlement")
    return {**result, "head": head.as_dict()}


def adopt_legacy_memory(*, configuration, reader, objects, head):
    """Reuse an audited, separately reviewed migration without another call."""
    events = load_chain(reader, head)
    result = load_acquisition_report(reader, head)
    saved = AcquisitionConfiguration.from_bytes(read_verified(reader,
        BlobRef.from_dict(thaw_json(events[0].payload)["configuration"])))
    old, wanted = saved.job.inputs, configuration.job.inputs
    checkpoint = configuration.job.attempt.model_policy.get("checkpoint")
    chunked = events[0].kind == "bootstrap_started"
    if ((not chunked and old.experience_mode != "compact-v1") or old.experience_proposals or
            old.experience != wanted.experience or old.experience_policy != wanted.experience_policy or
            checkpoint is None or checkpoint != saved.job.attempt.model_policy.get("checkpoint") or
            result["status"] != "completed"):
        raise RecordError("experience-bootstrap-context", "migration does not match this memory, policy and checkpoint")
    current = BlobRef.from_dict(result["materials"]["experience"])
    if not any(text.strip() for text in memory_value(reader, current,
            ExperiencePolicy.from_dict(wanted.experience_policy)).values()):
        raise RecordError("experience-bootstrap-empty", "legacy migration cannot erase all experience")
    closure = tuple(dict.fromkeys(ref for event in events
        for ref in (BlobRef.of(event.to_bytes()), *event.artifacts)))
    objects.publish(reader, closure)
    return current, head


def bootstrap_configuration(*, configuration, reader, objects):
    """Validate the whole migration before reserving a model server."""
    inputs = configuration.job.inputs
    prior = inputs.experience
    if inputs.experience_policy is None or prior is None:
        return None
    policy = ExperiencePolicy.from_dict(inputs.experience_policy)
    files = load_experience(reader, prior)
    if sum(len(file.data) for file in files) <= policy.memory_bytes:
        return None
    if policy.version == 2:
        from .experience_merge import prepare_chunked
        return prepare_chunked(configuration, reader, objects)
    guidance = objects.put(render("synthesis/source_acquirer/consolidate").encode())
    objects.publish(reader, (prior, *(file.reference.content for file in files)))
    configured = batch_configuration(configuration, prior, (), 0)
    configured = replace(configured, job=replace(configured.job,
        inputs=replace(configured.job.inputs, guidance=guidance),
        attempt=replace(configured.job.attempt,
                        attempt_id=configuration.job.attempt.attempt_id + ":legacy-memory")))
    configured.preflight(objects)
    return configured


def compact_legacy_memory(*, configuration, reader, objects, run, control=None):
    configured = bootstrap_configuration(configuration=configuration, reader=reader, objects=objects)
    if configured is None:
        return configuration.job.inputs.experience, None
    policy = ExperiencePolicy.from_dict(configured.job.inputs.experience_policy)
    if policy.version == 2:
        from .experience_merge import compact_chunks
        return compact_chunks(configuration=configured, reader=reader, objects=objects, run=run, control=control)
    run = memory_child(run.directory, configured)
    result = acquire_materials(configuration=configured, reader=objects, run=run, control=control)
    if result["status"] != "completed":
        raise RecordError("experience-bootstrap-incomplete", "legacy memory was not compacted; old memory retained")
    head, closure = run.seal("acquisition")
    objects.publish_owned(run.objects, closure)
    # Audit the sealed receipt and size before returning a replacement pointer.
    checked = load_acquisition_report(objects, head)
    current = BlobRef.from_dict(checked["materials"]["experience"])
    if not any(text.strip() for text in memory_value(objects, current, policy).values()):
        raise RecordError("experience-bootstrap-empty", "legacy migration cannot erase all experience")
    return current, head


def _parts(data, limit):
    start = 0
    while start < len(data):
        end = min(start + limit, len(data))
        if end < len(data):
            while data[end] & 0xC0 == 0x80:
                end -= 1
            # Prefer paragraphs, then lines, without creating tiny fragments.
            for boundary in (b"\n\n", b"\n"):
                split = data.rfind(boundary, start + (end - start) // 2, end)
                if split >= 0:
                    end = split + len(boundary)
                    break
        yield start, end, data[start:end].decode("utf-8")
        start = end


def legacy_chunks(reader, prior, policy):
    result = []
    for file in load_experience(reader, prior):
        name = file.reference.path.rsplit("/", 1)[-1][:-3]
        for start, end, text in _parts(file.data, policy.legacy_chunk_bytes):
            value = {"construction": "", "solver": "", name: text}
            files = experience_files(value)
            snapshot = experience_snapshot(files).to_bytes()
            result.append(({"view": name, "start": start, "end": end,
                            "snapshot": BlobRef.of(snapshot).as_dict()}, files, snapshot))
    return tuple(result)


def _publish_files(objects, files):
    for file in files:
        objects.put(file.data)
    return objects.put(experience_snapshot(files).to_bytes())


def chunk_configuration(config, row, index, count, guidance):
    policy = ExperiencePolicy.from_dict(config.job.inputs.experience_policy)
    small = replace(policy, memory_bytes=min(policy.delta_bytes, policy.memory_bytes))
    root = replace(config, job=replace(config.job, inputs=replace(config.job.inputs,
        guidance=guidance, experience_policy=small.as_dict(), pending_experience=()),
        attempt=replace(config.job.attempt, attempt_id=f"{config.job.attempt.attempt_id}:legacy-fragment:{index}:{count}")))
    return batch_configuration(root, BlobRef.from_dict(row["snapshot"]), (), 0)


def merge_configuration(config, deltas, guidance):
    return replace(config, job=replace(config.job, inputs=replace(config.job.inputs,
        guidance=guidance, experience_mode="merge-v1", experience=None,
        experience_proposals=tuple(deltas), pending_experience=()),
        attempt=replace(config.job.attempt, attempt_id=config.job.attempt.attempt_id + ":legacy-merge")))


def _delta(reader, outcome, policy):
    reference = BlobRef.from_dict(outcome["materials"]["experience"])
    files = delta_files(views(load_experience(reader, reference)), policy.delta_bytes)
    return BlobRef.of(experience_snapshot(files).to_bytes()), files


def prepare_chunked(configuration, reader, objects):
    inputs = configuration.job.inputs
    policy = ExperiencePolicy.from_dict(inputs.experience_policy)
    if policy.version != 2 or inputs.experience is None or inputs.experience_proposals or inputs.pending_experience:
        raise ValueError("legacy migration requires version 2 and an unmerged historical snapshot")
    files = load_experience(reader, inputs.experience)
    objects.publish(reader, (inputs.experience, *(f.reference.content for f in files)))
    chunks = legacy_chunks(reader, inputs.experience, policy)
    guide = objects.put(render("synthesis/source_acquirer/extract_legacy").encode())
    for index, (row, files, snapshot) in enumerate(chunks):
        _publish_files(objects, files)
        configured = chunk_configuration(configuration, row, index, len(chunks), guide)
        configured.preflight(objects)
    return configuration


def _started(reader, event):
    if event.kind != "bootstrap_started":
        raise RecordError("bootstrap-history", "missing migration start")
    row = thaw_json(event.payload)
    config = AcquisitionConfiguration.from_bytes(read_verified(reader, BlobRef.from_dict(row["configuration"])))
    policy = ExperiencePolicy.from_dict(config.job.inputs.experience_policy)
    if policy.version != 2 or config.job.inputs.experience is None:
        raise RecordError("bootstrap-context", "not a version 2 legacy migration")
    chunks = legacy_chunks(reader, config.job.inputs.experience, policy)
    if row["chunks"] != [item[0] for item in chunks]:
        raise RecordError("bootstrap-chunks", "migration changed or omitted original bytes")
    return config, policy, row


def _checked_child(reader, event, expected, kind):
    if event.kind != kind:
        raise RecordError("bootstrap-history", "unexpected migration stage")
    row = thaw_json(event.payload)
    child = load_chain(reader, BlobRef.from_dict(row["head"]))
    reference = BlobRef.of(expected.to_bytes()).as_dict()
    if row["configuration"] != reference or child[0].payload["configuration"] != reference:
        raise RecordError("bootstrap-context", "migration child changed its inputs or settings")
    outcome = load_acquisition_report(reader, BlobRef.from_dict(row["head"]))
    if outcome != row["outcome"] or outcome["status"] != "completed":
        raise RecordError("bootstrap-outcome", "migration child is not an accepted result")
    return outcome


def verify_pending_chunk(parent, ordinal, reference):
    events = parent.journal("acquisition").load()
    config, policy, start = _started(parent.objects, events[0])
    if len(events) != ordinal + 1 or ordinal >= len(start["chunks"]):
        raise RecordError("bootstrap-context", "fragment is not the next uncommitted migration stage")
    guide = BlobRef.from_dict(start["extract_guidance"])
    for index, event in enumerate(events[1:]):
        expected = chunk_configuration(config, start["chunks"][index], index, len(start["chunks"]), guide)
        _checked_child(parent.objects, event, expected, "bootstrap_chunk")
    expected = chunk_configuration(config, start["chunks"][ordinal], ordinal, len(start["chunks"]), guide)
    if reference != BlobRef.of(expected.to_bytes()):
        raise RecordError("bootstrap-context", "pending fragment differs from the migration plan")
    return config


def load_bootstrap_report(reader, head):
    events = load_chain(reader, head)
    if not events:
        raise RecordError("bootstrap-incomplete", "missing migration history")
    config, policy, start = _started(reader, events[0])
    count = len(start["chunks"])
    if len(events) != count + 3 or events[-1].kind != "bootstrap_finished":
        raise RecordError("bootstrap-incomplete", "not every fragment and final merge has settled")
    deltas = []
    for index, event in enumerate(events[1:1 + count]):
        expected = chunk_configuration(config, start["chunks"][index], index, count,
                                       BlobRef.from_dict(start["extract_guidance"]))
        outcome = _checked_child(reader, event, expected, "bootstrap_chunk")
        ref, files = _delta(reader, outcome, policy)
        read_verified(reader, ref)
        for file in files:
            read_verified(reader, file.reference.content)
        deltas.append(ref)
    expected = merge_configuration(config, deltas, BlobRef.from_dict(start["merge_guidance"]))
    merged = _checked_child(reader, events[-2], expected, "bootstrap_merge")
    if merged["materials"] is None:
        raise RecordError("bootstrap-empty", "no useful historical experience was retained")
    current = BlobRef.from_dict(merged["materials"]["experience"])
    if not any(value.strip() for value in memory_value(reader, current, policy).values()):
        raise RecordError("bootstrap-empty", "no useful historical experience was retained")
    result = {"status": "completed", "materials": {"experience": current.as_dict()},
              "prior": config.job.inputs.experience.as_dict(), "chunks": count}
    if thaw_json(events[-1].payload) != result:
        raise RecordError("bootstrap-result", "final memory differs from the verified merge")
    return {**result, "head": head.as_dict()}


def compact_chunks(*, configuration, reader, objects, run, control=None):
    control = AttemptControl() if control is None else control
    with run.owner():
        store, tape = run.objects, Tape(run.journal("acquisition"))
        ref = store.put(configuration.to_bytes())
        if tape.events:
            event = tape.take("bootstrap_started")
            saved, policy, start = _started(store, event)
            if saved.to_bytes() != configuration.to_bytes():
                raise RecordError("bootstrap-context", "saved migration has different inputs")
        else:
            inputs = configuration.job.inputs
            policy = ExperiencePolicy.from_dict(inputs.experience_policy)
            original = load_experience(reader, inputs.experience)
            store.publish(reader, (inputs.experience, *(f.reference.content for f in original)))
            chunks = legacy_chunks(store, inputs.experience, policy)
            closure = [ref, inputs.experience, *(f.reference.content for f in original)]
            for row, files, payload in chunks:
                closure.extend((_publish_files(store, files), *(f.reference.content for f in files)))
            extract = store.put(render("synthesis/source_acquirer/extract_legacy").encode())
            merge = store.put(render("synthesis/source_acquirer/consolidate").encode())
            start = {"configuration": ref.as_dict(), "chunks": [item[0] for item in chunks],
                     "extract_guidance": extract.as_dict(), "merge_guidance": merge.as_dict()}
            tape.record("bootstrap_started", start, (*closure, extract, merge))

        def stage(name, configured, directory):
            if tape.cursor < len(tape.events):
                event = tape.take(name)
                return _checked_child(store, event, configured, name)
            if control.cancelled():
                raise RecordError("bootstrap-interrupted", "migration paused; accepted fragments retained")
            child = memory_child(directory, configured) if name == "bootstrap_chunk" else JournalDirectory(directory)
            outcome = acquire_materials(configuration=configured, reader=store, run=child, control=control)
            if outcome["status"] != "completed":
                raise RecordError("bootstrap-incomplete", "migration incomplete; original and accepted fragments retained")
            head, closure = child.seal("acquisition")
            store.publish_owned(child.objects, closure)
            config_ref = store.put(configured.to_bytes())
            tape.record(name, {"configuration": config_ref.as_dict(), "head": head.as_dict(),
                               "outcome": outcome}, (config_ref, *closure))
            return outcome

        deltas = []
        for index, row in enumerate(start["chunks"]):
            configured = chunk_configuration(configuration, row, index, len(start["chunks"]),
                                              BlobRef.from_dict(start["extract_guidance"]))
            outcome = stage("bootstrap_chunk", configured, run.directory / "chunks" / f"{index:04d}")
            delta, files = _delta(store, outcome, policy)
            _publish_files(store, files)
            deltas.append(delta)
        configured = merge_configuration(configuration, deltas, BlobRef.from_dict(start["merge_guidance"]))
        merged = stage("bootstrap_merge", configured, run.directory / "experience")
        if merged["materials"] is None or not any(value.strip() for value in memory_value(store,
                BlobRef.from_dict(merged["materials"]["experience"]), policy).values()):
            raise RecordError("bootstrap-empty", "legacy migration cannot erase all experience")
        current = BlobRef.from_dict(merged["materials"]["experience"])
        tape.record("bootstrap_finished", {"status": "completed", "materials": {"experience": current.as_dict()},
            "prior": configuration.job.inputs.experience.as_dict(), "chunks": len(start["chunks"])})
        tape.finish()
        head, closure = run.seal("acquisition")
        load_bootstrap_report(store, head)
        objects.publish_owned(store, closure)
        return current, head
