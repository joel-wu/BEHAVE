"""What an instrumented model may reach.

The source is written by an agent and is the thing under test. It used to run
through `exec` with a namespace carrying no `__builtins__` of its own, which is
how Python hands over the real ones, so a model could open a file, call `eval`,
or import anything. That is a hole in the proof before it is one in the
machine: a model that reads a file has left the semantics the certificate is
about.
"""
import pytest

from behave.sim.behavior import instrumented


def load(tmp_path, source):
    path = tmp_path / "behavior.py"
    path.write_text(source)
    return instrumented.load_instrumented(path)


def model(body):
    return ("class M:\n"
            "    def reset(self):\n"
            "        self.z = 0\n"
            "    def process(self, a):\n"
            f"        {body}\n"
            "        return {'y': a}\n")


def test_an_admitted_model_still_loads_and_runs(tmp_path):
    ns, _sites, _hits = load(tmp_path, model("z = len(range(3))"))
    cls = next(v for v in ns.values()
               if isinstance(v, type) and hasattr(v, "process"))
    m = cls()
    m.reset()
    assert m.process(4) == {"y": 4}


@pytest.mark.parametrize("body,raises", [
    ("z = open('/etc/passwd')", NameError),
    ("z = eval('1+1')", NameError),
    ("z = exec('pass')", NameError),
    ("z = globals()", NameError),
    # `__import__` is wrapped rather than removed, since an import statement
    # calls it and the approved library has to remain importable. So the way
    # out is closed by the guard rather than by the name being absent.
    ("z = __import__('os')", ImportError),
])
def test_the_ways_out_are_not_there(tmp_path, body, raises):
    ns, _sites, _hits = load(tmp_path, model(body))
    cls = next(v for v in ns.values()
               if isinstance(v, type) and hasattr(v, "process"))
    m = cls()
    m.reset()
    with pytest.raises(raises):
        m.process(1)


def test_an_unapproved_import_is_refused_at_load(tmp_path):
    with pytest.raises(ImportError, match="not an approved library"):
        load(tmp_path, "import os\n" + model("z = 1"))


def test_a_registered_attribute_read_is_not_mistaken_for_host_io(tmp_path):
    # Concrete source diagnostics already expose getattr; symbolic lowering
    # remains a separate capability. Keep host-I/O negatives above unchanged.
    source = model("z = getattr(self, 'z')").replace("return {'y': a}", "return {'y': z}")
    namespace, _, _ = load(tmp_path, source)
    instance = namespace["M"]()
    instance.reset()
    assert instance.process(4) == {"y": 0}


def test_the_approved_library_still_imports(tmp_path):
    ns, _sites, _hits = load(
        tmp_path, "from fp_lib import fp32_add\n" + model("z = 1"))
    assert "fp32_add" in ns
