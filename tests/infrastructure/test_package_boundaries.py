"""The installed package must not depend on checkout-only operator scripts."""

import ast
import json
import re
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize('linked', [False, True], ids=['directories', 'symlinks'])
def test_release_ignores_runtime_outputs_but_keeps_required_sources(tmp_path, linked):
    repo = Path(__file__).resolve().parents[2]
    (tmp_path / '.gitignore').write_text((repo / '.gitignore').read_text())
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    ignored = ['work', 'results', 'submission', 'external/external']
    target = tmp_path / 'storage'
    target.mkdir()
    for name in ignored:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if linked:
            path.symlink_to(target, target_is_directory=True)
        else:
            path.mkdir()
    ignored.append('BEHAVE_Supplementary Material (1).zip')
    required = ['src/behave/runtime/jobs.py',
                'src/behave/agent/prompts/synthesis/task_contract.md',
                'infra/patches/required.patch', 'docs/data/review.json',
                'data/releases/index.json', 'configs/train/example.json']
    result = subprocess.run(
        ['git', 'check-ignore', '--no-index', '--stdin'], cwd=tmp_path,
        input='\n'.join(ignored + required) + '\n', text=True,
        capture_output=True, check=True)
    assert set(result.stdout.splitlines()) == set(ignored)


def operator_imports(source, *, roots=('tools',)):
    found = []
    for node in ast.walk(ast.parse(source)):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        elif isinstance(node, ast.Call) and node.args:
            function = node.func
            dynamic = (isinstance(function, ast.Name) and function.id in {'__import__', 'import_module'}
                       or isinstance(function, ast.Attribute) and function.attr == 'import_module')
            if dynamic and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                names = [node.args[0].value]
        found.extend((node.lineno, name) for name in names if name.split('.')[0] in roots)
    return found


def test_installed_package_has_no_operator_imports():
    root = Path(__file__).resolve().parents[2] / 'src/behave'
    violations = [(str(path.relative_to(root)), line, name)
                  for path in sorted(root.rglob('*.py'))
                  for line, name in operator_imports(path.read_text(), roots=('tools', 'work', 'results'))]
    assert not violations, f'Package code cannot import checkout-only launchers: {violations}'


def test_shared_train_eval_runtime_have_no_current_deployment_paths():
    root = Path(__file__).resolve().parents[2] / 'src/behave'
    # These are deployment identities, not portable defaults or algorithm settings.
    private = ('/home/yuheng', '/store/users/yuheng', 'pool-8p3iw3lz0-')
    violations = [(str(path.relative_to(root)), prefix)
                  for area in ('train', 'eval', 'runtime')
                  for path in (root / area).rglob('*.py')
                  for prefix in private if prefix in path.read_text()]
    assert not violations, f'Move deployment identities to explicit machine inputs: {violations}'


@pytest.mark.dataset
def test_dataset_provenance_uses_public_sources_and_included_notices():
    root = Path(__file__).resolve().parents[2]
    tasks = root / 'data/problems'
    for path in tasks.glob('*/*/*'):
        if path.is_file():
            assert 'docs/data/' not in path.read_text(), path
    for path in tasks.glob('*/*/source.json'):
        for source in json.loads(path.read_text()).get('provenance', []):
            assert re.fullmatch(r'[0-9a-f]{40}', source['revision']), path
            assert source['url'].startswith(
                source['repository'].rstrip('/') + '/blob/' + source['revision'] + '/'), path
            notice = Path(source['license_file'])
            assert not notice.is_absolute() and '..' not in notice.parts, path
            assert (root / notice).is_file(), path
