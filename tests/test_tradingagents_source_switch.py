"""Exercise actual pip origin replacement offline, in an isolated virtualenv."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import textwrap
import venv
import zipfile


def wheel(directory, name, version, content, dependencies=''):
    filename = f'{name}-{version}-py3-none-any.whl'
    metadata = f'{name}-{version}.dist-info'
    with zipfile.ZipFile(directory / filename, 'w') as archive:
        archive.writestr(f'{name}/__init__.py', content)
        archive.writestr(f'{metadata}/METADATA', f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n{dependencies}')
        archive.writestr(f'{metadata}/WHEEL', 'Wheel-Version: 1.0\nGenerator: source-switch-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        archive.writestr(f'{metadata}/RECORD', '')
    return filename


def test_real_pip_git_to_same_version_index_switch_preserves_unrelated_distribution(tmp_path):
    root = Path(__file__).resolve().parents[1]
    local_index = tmp_path / 'index'
    local_index.mkdir()
    wheel(local_index, 'tradingagents', '0.6.0', "ORIGIN = 'index'\n", 'Requires-Dist: source-sentinel>=1\n')
    sentinel = wheel(local_index, 'source_sentinel', '1.0', "SENTINEL = 'do not reinstall'\n")
    repository = tmp_path / 'git-source'
    repository.mkdir()
    (repository / 'pyproject.toml').write_text('[build-system]\nrequires = []\nbuild-backend = "fixture_build"\nbackend-path = ["."]\n')
    # A tiny standard-library-only fixture backend; no registry or build downloads.
    (repository / 'fixture_build.py').write_text(textwrap.dedent('''\
        import pathlib, zipfile
        def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
            name = 'tradingagents-0.6.0-py3-none-any.whl'
            info = 'tradingagents-0.6.0.dist-info'
            with zipfile.ZipFile(pathlib.Path(wheel_directory) / name, 'w') as z:
                z.writestr('tradingagents/__init__.py', "ORIGIN = 'git'\\n")
                z.writestr(info + '/METADATA', 'Metadata-Version: 2.1\\nName: tradingagents\\nVersion: 0.6.0\\n')
                z.writestr(info + '/WHEEL', 'Wheel-Version: 1.0\\nGenerator: fixture\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n')
                z.writestr(info + '/RECORD', '')
            return name
        '''))
    virtualenv = tmp_path / 'venv'
    venv.EnvBuilder(with_pip=True).create(virtualenv)
    python = virtualenv / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    env = {key: value for key, value in os.environ.items() if not key.startswith(('PYTHON', 'PIP_', 'GIT_CONFIG'))}
    env.update(PIP_NO_INDEX='1', PIP_FIND_LINKS=str(local_index), PIP_DISABLE_PIP_VERSION_CHECK='1', PIP_CONFIG_FILE=os.devnull)
    def run(*args, cwd=tmp_path):
        result = subprocess.run(list(map(str, args)), cwd=cwd, env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout
    run('git', 'init', repository)
    run('git', 'add', '.', cwd=repository)
    run('git', '-c', 'user.name=Source Switch Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'Fixture', cwd=repository)
    commit = run('git', 'rev-parse', 'HEAD', cwd=repository).strip()
    run(python, '-m', 'pip', 'install', local_index / sentinel)
    run(python, '-m', 'pip', 'install', '--no-deps', 'git+' + repository.as_uri() + '@' + commit)
    inspect = '''import importlib.metadata as m, json, pathlib, tradingagents, source_sentinel
s = pathlib.Path(source_sentinel.__file__)
d = pathlib.Path(m.distribution('source-sentinel')._path) / 'INSTALLER'
print(json.dumps(dict(origin=tradingagents.ORIGIN, direct=m.distribution('tradingagents').read_text('direct_url.json'), sentinel=s.stat().st_mtime_ns, installer=d.stat().st_mtime_ns)))'''
    before = json.loads(run(python, '-c', inspect))
    assert before['origin'] == 'git'
    assert json.loads(before['direct'])['vcs_info']['commit_id'] == commit
    # This is the original bug: ordinary pip leaves the Git build installed.
    run(python, '-m', 'pip', 'install', 'tradingagents==0.6.0')
    assert json.loads(run(python, '-c', inspect)) == before
    project = tmp_path / 'panwatch'
    (project / 'scripts').mkdir(parents=True)
    shutil.copyfile(root / 'scripts/prepare-tradingagents-requirements.py', project / 'scripts/prepare-tradingagents-requirements.py')
    (project / 'requirements-runtime.txt').write_text('tradingagents==0.6.0\nsource-sentinel==1.0\n')
    run(python, 'scripts/prepare-tradingagents-requirements.py', '--source', '0.6.0', '--install', cwd=project)
    after = json.loads(run(python, '-c', inspect))
    assert after == {**before, 'origin': 'index', 'direct': None}
