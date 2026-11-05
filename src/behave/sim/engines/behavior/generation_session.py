"""One authenticated Behavior context, active Probe seed and latest feedback.

Preparation and the latest complete local UNSAT question per target group belong to this session's
fixed source and contract. Each seed still has its own native source/IR audit;
reuse requires identical state, targets, input domain and relevant history.
Suspended construction and complete formulae stay with one selection. Issued
preparation recipes retain input values, not native-state histories. No solver
answer or historical score is reused.
"""

import hashlib
from dataclasses import dataclass

from ...behavior.goal_projection import literal_false_goals
from ...core.record_value import canonical_json
from ...job_session import serve as serve_session
from ...job_session import stdio
from ..local_ir import _LocalQueryPreparation
from .generation_job import _build_with_context, _layout, _observe_trace, _targets, selected_trace

VERSION = "behavior-generation-session/11"


@dataclass(frozen=True)
class _TraceKey:
    canonical: str
    digest: str

    @classmethod
    def from_payload(cls, payload):
        key = canonical_json({"seed": payload["seed"], "trace": payload["trace"]})
        return cls(key, hashlib.sha256(key.encode()).hexdigest())


class PreparedGeneration:
    def __init__(self, payload):
        self.built, _image, _reused = _build_with_context(payload, extras={"targets"})
        self.targets = _targets(payload["targets"], self.built[6])
        self.ruled_out = (literal_false_goals(self.built[4].proc)
                          if self.built[7] is not None else ())
        self.queries = (_LocalQueryPreparation(self.built[4].proc, self.built[4].env)
                        if self.built[7] is not None else None)
        self.selected = None
        self.key = None
        self.probing = None
        self.confirmed = set()
        self.proved_questions = {}
        # Only the input recipes already issued by Select, keyed by its exact
        # source-bound trace digest. Donor discovery must not change a pending
        # request when another seed is observed or the old trace is restored.
        # These retain neither native states nor formulas/solver answers.
        self.preparation_recipes = {}
        self._material_binding = None

    def layout(self):
        return _layout(self.built, ruled_out=self.ruled_out)

    def prepare(self, payload):
        """Collect/share proposal material without selecting or certifying goals.

        Recorded schedules can differ. Only source input material is shared;
        every later Select and Probe retains its own exact schedule and audit.
        """
        if type(payload) is not dict or set(payload) not in (
                {"seed", "trace"}, {"material"}, {"updates"}):
            raise ValueError("preparation observes one trace or exchanges one material image")
        if 'updates' in payload and payload['updates'] is not True:
            raise ValueError('preparation updates require an explicit delta export')
        if self.queries is None or not self.built[4].proc.state:
            if payload.get('material') is not None:
                raise ValueError("this source has no state preparation material")
            return None
        from .preparation_frontier import PreparationFrontier

        owner = self.queries
        if owner._preparation_frontier is None:
            owner._preparation_frontier = PreparationFrontier(owner.command_preparations())
        frontier = owner._preparation_frontier
        if not any(row[4] for rows in frontier.effects.values() for row in rows):
            return None
        if "seed" in payload:
            *_, occurrences = _observe_trace(self.built, payload['seed'], payload['trace'])
            frontier.observe(occurrences)
            return None
        if self._material_binding is None:
            contract, spec, target, reference, instance, manifest, *_ = self.built
            self._material_binding = hashlib.sha256(canonical_json((contract.digest, spec.max_body_steps,
                target, reference.digest, instance.identity, manifest.digest())).encode()).hexdigest()
        binding = self._material_binding
        if 'updates' in payload:
            writers, commands = frontier.updates()
            return dict(version='behavior-preparation-material/3', binding=binding,
                        writers=writers, commands=commands)
        material = payload['material']
        if material is not None:
            if (type(material) is not dict or set(material) != {'version', 'binding', 'writers', 'commands'}
                    or material['version'] != 'behavior-preparation-material/3'
                    or material['binding'] != binding):
                raise ValueError("preparation material belongs to another source or transaction bound")
            frontier.extend(material['writers'], material['commands'])
            return None  # Import acknowledgment must not retransmit the full pool.
        frontier.updates()  # Full export already includes every pending row.
        return dict(version='behavior-preparation-material/3', binding=binding,
                    writers=frontier.rows(), commands=frontier.command_rows())

    def _key(self, payload, extras=()):
        if type(payload) is not dict or set(payload) != {"seed", "trace", "targets", *extras}:
            raise ValueError("a Behavior session query has its exact fields")
        targets = _targets(payload["targets"], self.targets)
        # Full canonical bytes, not a digest alone; source/context cannot be
        # supplied by a query. Retained states belong only to this worker.
        return _TraceKey.from_payload(payload), targets

    @staticmethod
    def _confirmation(key, targets):
        return key.digest, targets

    def _retained(self, key):
        if key == self.key:
            return self.selected
        if self.probing is not None and key == self.probing[0]:
            return self.probing[1]
        return None

    def select(self, payload):
        key, targets = self._key(payload, {'preparation_plan'} if 'preparation_plan' in payload else ())
        recipes = payload.get('preparation_plan', self.preparation_recipes.get(key.digest))
        selected = self._retained(key)
        if selected is None or selected.replay is None:
            self.selected = None
            self.key = None
            selected = selected_trace(self.built, payload["seed"], payload["trace"],
                                     payload["targets"], ruled_out=self.ruled_out,
                                     preparation=self.queries,
                                     proved_questions=self.proved_questions,
                                     preparation_recipes=recipes)
        else:
            if 'preparation_plan' in payload:
                from .preparation_frontier import recipe_image

                if payload['preparation_plan'] != recipe_image(
                        selected._preparation_binding, selected.preparation_recipes):
                    raise ValueError('a retained selection cannot change its issued preparation plan')
            selected.select(payload["targets"])
        self.selected, self.key = selected, key
        recipes = getattr(selected, 'preparation_recipes', None)
        if recipes is not None:
            self.preparation_recipes[key.digest] = recipes
        self.confirmed.add(self._confirmation(key, targets))
        return self.selected.as_dict()

    def probe(self, payload):
        if type(payload) is dict and "retained" in payload:
            if set(payload) != {"retained", "targets", "request", "timeout_ms"}:
                raise ValueError("a retained Behavior Probe has its exact fields")
            targets = _targets(payload["targets"], self.targets)
            if payload["retained"] == "selected":
                key, selected = self.key, self.selected
            elif payload["retained"] == "probing" and self.probing is not None:
                key, selected = self.probing
            else:
                raise ValueError("Probe must name an available retained context")
            if key is None or selected is None:
                raise ValueError("Probe must name an available retained context")
        else:
            key, targets = self._key(payload, extras={"request", "timeout_ms"})
            selected = self._retained(key)
        if self._confirmation(key, targets) not in self.confirmed:
            raise ValueError("Probe must name a confirmed Select seed and trace")
        if selected is None:
            # Baseline-first selection visits several seeds before the first
            # query. An evicted seed is reconstructed from its confirmed trace;
            # no caller-supplied state or new feedback is trusted.
            selected = selected_trace(self.built, payload["seed"], payload["trace"],
                                     payload["targets"], verify_feedback=False,
                                     ruled_out=self.ruled_out, preparation=self.queries,
                                     proved_questions=self.proved_questions,
                                     preparation_recipes=self.preparation_recipes.get(key.digest))
        else:
            selected.select(payload["targets"])
        self.selected, self.key = selected, key
        # Candidate feedback may Select another trace between two requests from
        # this seed. Keep its deterministic states until Probe advances to a
        # different seed, not all baseline/generated histories. Every query
        # still validates its request; only an identical proved-local-UNSAT
        # question in this source/contract session can omit the solver call.
        self.probing = key, selected
        return selected.probe(payload["request"], payload["timeout_ms"])


def serve(reader, writer):
    prepared = None

    def dispatch(op, payload):
        nonlocal prepared
        if op == "open":
            prepared = PreparedGeneration(payload)
            return prepared.layout()
        if op == "select":
            return prepared.select(payload)
        if op == "prepare":
            return prepared.prepare(payload)
        if op == "probe":
            return prepared.probe(payload)
        raise ValueError("invalid Behavior session operation")

    serve_session(reader, writer, version=VERSION, dispatch=dispatch)


if __name__ == "__main__":
    stdio(serve)
