"""Public task-extension path from declarative semantics to candidate stimuli."""

from __future__ import annotations

import pytest

from behave.sim import api


def _catalogue():
    goals = ("extension.sequence.ab", "extension.value.one")
    return api.CatalogueManifest.from_dict({
        "version": "catalogue-manifest/1",
        "goals": [
            {
                "id": goal,
                "cls": "coverage",
                "family": "custom-finite",
                "provider": "task-extension",
                "event": "sample",
                "policy": "custom-finite/1",
                "evidence": "trace",
                "provenance": [],
            }
            for goal in goals
        ],
        "occurrences": [
            {
                "key": {
                    "provider": "task-extension",
                    "path": ["declaration", index],
                    "role": "hit",
                },
                "goal": goal,
            }
            for index, goal in enumerate(goals)
        ],
    })


def _contract():
    u8 = api.FiniteType(8)
    return api.TaskContract(
        endpoints=(
            api.Endpoint(
                "in", api.Direction.IN,
                (api.Field("x", u8, "in_x"),
                 api.Field("event0", u8, "in_event0"),
                 api.Field("event1", u8, "in_event1")),
                api.ready_valid("in", "in_valid", "in_ready"),
                clock="clk", kind=api.Kind.TRANSACTION),
            api.Endpoint(
                "out", api.Direction.OUT, (api.Field("y", u8, "out_y"),),
                api.ready_valid("out", "out_valid", "out_ready"),
                clock="clk", kind=api.Kind.TRANSACTION),
        ),
        clocks=(api.ClockDomain("clk", "clk", "rst"),),
        source="declarative-generation public test",
    )


def _task(contract):
    ports = (
        api.Port("in_x", 8, driven_by="environment", clock="clk"),
        api.Port("in_event0", 8, driven_by="environment", clock="clk"),
        api.Port("in_event1", 8, driven_by="environment", clock="clk"),
        api.Port("out_y", 8, driven_by="artifact", clock="clk"),
        api.Port("in_valid", 1, driven_by="environment"),
        api.Port("in_ready", 1, driven_by="artifact"),
        api.Port("out_valid", 1, driven_by="artifact"),
        api.Port("out_ready", 1, driven_by="environment"),
        api.Port("clk", 1, driven_by="environment"),
        api.Port("rst", 1, driven_by="environment"),
    )
    return api.admit(contract, api.Realization(
        "device", ports,
        channel_ports={
            "in": ["in_x", "in_event0", "in_event1"],
            "out": ["out_y"],
        },
        identity="declarative-generation-device",
    ))


def _replace(seed, replacements):
    queues = {name: [list(row) for row in rows]
              for name, rows in seed.queues}
    for field, value in replacements.items():
        queues["in"][0][field] = value
    return api.Stimulus(
        queues={name: [tuple(row) for row in rows]
                for name, rows in queues.items()},
        slots=seed.slots,
        memory_policy=dict(seed.memory_policy),
        memory_environment=seed.memory_environment,
        initial_state=dict(seed.initial_state),
        source=seed.source,
        clock_schedule=seed.clock_schedule,
    )


def test_public_declarative_guidance_realizes_candidates_in_common_scheduler():
    catalogue = _catalogue()
    value_goal = "extension.value.one"
    sequence_goal = "extension.sequence.ab"
    predicate = api.FinitePredicate(
        value_goal, (api.FiniteVariable("x", (0, 1)),), ((1,),))
    monitor = api.FiniteMonitor(
        states=("idle", "seen-a"), events=(1, 2), initial="idle",
        goals=(sequence_goal,),
        transitions=(
            api.FiniteMonitorTransition("idle", 1, "seen-a"),
            api.FiniteMonitorTransition("idle", 2, "idle"),
            api.FiniteMonitorTransition("seen-a", 1, "seen-a"),
            api.FiniteMonitorTransition(
                "seen-a", 2, "idle", (sequence_goal,)),
        ),
    )
    semantics = api.DeclarativeGoalSemantics((predicate,), (monitor,))
    guidance = api.DeclarativeGuidanceAdapter(semantics, catalogue)
    schema = api.GoalSchemaDefinition(
        "custom-finite", 1,
        required=(api.DECLARATION, api.CONCRETE_HIT, api.LOCAL_GUIDANCE,
                  api.EXACT_BOUNDED_HIT),
    )
    provider = api.GoalProvider.declarative(
        api.GoalProviderIdentity("task-extension", 1),
        schema, catalogue, semantics, guidance=guidance)
    policy = api.GoalProviderRegistry((schema,), (provider,)).freeze(
        "custom-finite-policy/1",
        (api.GoalPolicySelection(schema, api.ENABLED, (provider,)),),
    )

    with pytest.raises(TypeError, match="versioned identity"):
        api.DeclarativeCandidateRealizer(
            "unversioned", lambda *_args: None, lambda *_args: None)

    contract = _contract()
    task = _task(contract)
    seed = api.reset_then(
        contract, ((0, 0, 0),), controls={"out_ready": 1},
        drain=1, source="baseline")
    offered = next(index for index, slot in enumerate(seed.slots)
                   if "in" in slot.offers)

    def mapping(_seed, witness):
        fields = ((0,) if witness.goal == value_goal else (1, 2))
        return api.DeclarativeCandidateMapping(
            f"{witness.goal}@{offered}",
            tuple(api.StimulusField("in", 0, field, offered)
                  for field in fields),
        )

    def candidate(current, witness, _mapping):
        if witness.goal == value_goal:
            predicates, _events = witness.steps[0].concrete_inputs()
            return _replace(current, {0: predicates[value_goal]["x"]})
        events = [dict(step.monitor_events)[monitor.key]
                  for step in witness.steps]
        return _replace(current, {1: events[0], 2: events[1]})

    bridge = api.DeclarativeGenerationProvider(
        policy, provider,
        api.DeclarativeCandidateRealizer(
            "task-queue-realization/1", mapping, candidate),
    )
    spec = api.GenerationSpec(
        len(seed.slots), 1, contract.clocks[0].reset_hold_cycles, 1)
    # The worklist hands off to the new seed after each completed query. With no
    # replay feedback it asks the sequence goal again (a duplicate candidate), so
    # the baseline's value-goal question is the third probe.
    generated = api.generate_stimuli(
        task, (seed,), (bridge,), catalogue=catalogue,
        generation_spec=spec, probe_budget=3,
        baseline_policy="public-test-baseline/1",
    )

    values = {tuple(dict(stimulus.queues)["in"][0])
              for stimulus in generated.stimuli}
    assert values == {(0, 0, 0), (1, 0, 0), (0, 1, 2)}
    search = generated.searches[0]
    telemetry = dict(search.telemetry.items())
    assert dict(telemetry["outcomes"])["sat"] == 3
    assert "hits" not in telemetry, \
        "candidate generation cannot publish replay evidence"
    assert bridge.goal_capability_use().targets == catalogue
    assert all(
        dict(row)["scope"] == "path-prefix"
        for rows in generated.provenance.values() for row in rows)

    # The realizer is only a candidate source.  Even a callback that returns a valid
    # Stimulus cannot smuggle a change outside its typed mapping past the common
    # scheduler, and it has no API on which to publish a hit.
    bad = api.DeclarativeGenerationProvider(
        policy, provider,
        api.DeclarativeCandidateRealizer(
            "dishonest-task-queue-realization/1",
            lambda _seed, witness: api.DeclarativeCandidateMapping(
                f"bad:{witness.goal}@{offered}",
                (api.StimulusField("in", 0, 0, offered),)),
            lambda current, _witness, _mapping: _replace(current, {1: 2}),
        ),
    )
    rejected = api.generate_stimuli(
        task, (seed,), (bad,), catalogue=catalogue,
        generation_spec=spec, probe_budget=1,
        baseline_policy="public-test-baseline/1",
    )
    assert rejected.stimuli == (seed,)
    bad_telemetry = dict(rejected.searches[0].telemetry.items())
    assert bad_telemetry["candidate_refusals"] == 1
    assert dict(bad_telemetry["refusals"])[
        "generation-candidate-mutates-nonselected-field"] == 1


def test_release_declarative_rows_execute_registered_generation_bridge():
    """Each release extension row reaches the common production scheduler."""

    from tests.support.goal_policies import goal_policy_fixtures

    policy = goal_policy_fixtures()["declarative-standard-extensions/v2"]
    contract = _contract()
    task = _task(contract)
    seed = api.reset_then(
        contract, ((0, 0, 0),), controls={"out_ready": 1},
        drain=1, source="release declarative baseline")
    offered = next(index for index, slot in enumerate(seed.slots)
                   if "in" in slot.offers)
    spec = api.GenerationSpec(
        len(seed.slots), 1, contract.clocks[0].reset_hold_cycles, 1)

    for provider in policy.enabled_providers(api.LOCAL_GUIDANCE):
        semantics = provider.semantics
        assert semantics is not None
        provider_key = provider.identity.key

        def mapping(_seed, witness, *, semantics=semantics,
                    provider_key=provider_key):
            fields = ((0,) if semantics.predicates else
                      tuple(range(1, 1 + len(witness.steps))))
            return api.DeclarativeCandidateMapping(
                f"release:{provider_key}@{offered}",
                tuple(api.StimulusField("in", 0, field, offered)
                      for field in fields),
            )

        def candidate(current, selected, _mapping, *, semantics=semantics):
            if semantics.predicates:
                predicates, _events = selected.steps[0].concrete_inputs()
                field, value = next(iter(predicates[selected.goal].items()))
                assert field == semantics.predicates[0].variables[0].name
                return _replace(current, {0: value})
            event_codes = {"a": 1, "b": 2}
            events = [
                dict(step.monitor_events)[semantics.monitors[0].key]
                for step in selected.steps
            ]
            return _replace(current, {
                field: event_codes[event]
                for field, event in enumerate(events, start=1)
            })

        bridge = api.DeclarativeGenerationProvider(
            policy, provider,
            api.DeclarativeCandidateRealizer(
                f"release-realizer/{provider.identity.key}",
                mapping, candidate),
        )
        generated = api.generate_stimuli(
            task, (seed,), (bridge,), catalogue=policy.manifest,
            generation_spec=spec, probe_budget=1,
            baseline_policy="release-declarative-baseline/1",
        )

        assert len(generated.stimuli) == 2
        assert generated.searches[0].generated
        capability = bridge.goal_capability_use()
        assert capability.source_catalogue is policy.manifest
        assert bridge.catalogue_manifest() is policy.manifest
        assert tuple(capability.targets) == \
            tuple(provider.catalogue)
