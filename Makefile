# Development checks. Each target is one question; none of them infers the host.
#   make check          lint and the unit lane (no simulator, solver or isolation backend)
#   make check-node     lanes that need the EDA toolchain and the namespace isolation backend
#   make check-pipelines  optional ChipMate/MAGE adapters and pinned upstream sources
#   make check-install  build the wheel, install it into a fresh environment and walk the README path
PYTHON ?= .venv/bin/python
WORKERS ?= 8
UNIT_MARKERS = not solver and not eda and not isolation and not cosim and not locked_env and not dataset \
               and not native_training and not gpu_training and not cluster_training and not pipelines
NODE_MARKERS = (solver or eda or isolation or cosim) and not locked_env and not dataset \
               and not native_training and not gpu_training and not cluster_training and not pipelines
# Syntax errors, undefined names, unused and redefined imports. Tests skip the import rules because
# pytest fixtures are imported and then shadowed by parameters.
LINT_RULES = E9,F63,F7,F82,F401,F811,F821
TEST_LINT_RULES = E9,F63,F7,F82

.PHONY: check lint check-node check-pipelines check-install

check: lint
	$(PYTHON) -m pytest tests/infrastructure -n $(WORKERS) -m "$(UNIT_MARKERS)" -p no:cacheprovider -v

lint:
	$(PYTHON) -m ruff check --select $(LINT_RULES) src tools examples scripts
	$(PYTHON) -m ruff check --select $(TEST_LINT_RULES) tests

check-node:
	$(PYTHON) -m pytest tests/infrastructure -n $(WORKERS) -m "$(NODE_MARKERS)" -p no:cacheprovider -v

check-pipelines:
	$(PYTHON) -c "import pydantic, rich; from behave.pipelines.upstream import module; module('chipmate', 'inference'); module('mage', 'agent')"
	$(PYTHON) -m pytest tests/infrastructure -n $(WORKERS) -m pipelines -p no:cacheprovider -v

check-install:
	bash scripts/check_install.sh "$(CURDIR)" "$(OUT)"
