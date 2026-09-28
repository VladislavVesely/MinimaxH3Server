"""All filesystem fixtures are confined to pytest's temporary directory."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


def test_default_and_explicit_root_expansion(isolated_home):
    from h3server.paths import AppPaths

    assert AppPaths().root == isolated_home / "MinimaxH3Server"
    assert AppPaths("~/custom").root == isolated_home / "custom"
    assert not (isolated_home / "MinimaxH3Server").exists()
    assert not (isolated_home / "custom").exists()


def test_relative_root_is_resolved(tmp_path, monkeypatch):
    from h3server.paths import AppPaths

    monkeypatch.chdir(tmp_path)
    assert AppPaths("local/../app").root == tmp_path / "app"


def test_directory_enumeration_and_explicit_paths(tmp_path):
    from h3server.paths import AppPaths

    paths = AppPaths(tmp_path / "app")
    names = {
        "releases", "environments", "bootstrap", "bin", "config", "state", "models",
        "loras", "cache", "cache/huggingface", "cache/torch", "cache/pip", "cache/cuda",
        "cache/triton", "logs", "tmp", "tmp/downloads", "tmp/jobs",
    }
    assert set(paths.directories()) == {paths.root} | {paths.root / name for name in names}
    assert len(paths.directories()) == len(names) + 1
    assert paths.current == paths.root / "current"
    assert paths.current not in paths.directories()
    for name in names:
        attribute = name.replace("/", "_")
        assert getattr(paths, attribute) == paths.root / name
    assert not paths.root.exists()


def test_cache_environment_is_explicit_and_does_not_mutate_process(tmp_path):
    from h3server.paths import AppPaths

    paths = AppPaths(tmp_path / "app")
    before = dict(os.environ)
    env = paths.cache_environment()
    expected = {
        "XDG_CACHE_HOME": "cache", "HF_HOME": "cache/huggingface",
        "HF_HUB_CACHE": "cache/huggingface/hub", "HF_DATASETS_CACHE": "cache/huggingface/datasets",
        "HF_ASSETS_CACHE": "cache/huggingface/assets", "TORCH_HOME": "cache/torch",
        "TORCH_EXTENSIONS_DIR": "cache/torch/extensions", "PIP_CACHE_DIR": "cache/pip",
        "CUDA_CACHE_PATH": "cache/cuda", "TRITON_CACHE_DIR": "cache/triton",
        "TMPDIR": "tmp", "TEMP": "tmp", "TMP": "tmp",
    }
    assert env == {key: str(paths.root / suffix) for key, suffix in expected.items()}
    assert "HOME" not in env
    assert os.environ == before
    assert not paths.root.exists()


def test_job_paths_are_confined(tmp_path):
    from h3server.paths import AppPaths

    paths = AppPaths(tmp_path / "app")
    assert paths.job_dir("job-123") == paths.tmp_jobs / "job-123"
    assert paths.job_path("job-123", "output/video.mp4") == paths.tmp_jobs / "job-123/output/video.mp4"
    assert not paths.root.exists()


@pytest.mark.parametrize("job_id", ["", ".", "..", "../escape", "/absolute", "a/b", "a\\b", "C:\\escape", "a:stream", "a\x00b"])
def test_job_identifiers_reject_traversal_and_nonportable_paths(tmp_path, job_id):
    from h3server.paths import AppPaths

    with pytest.raises(ValueError):
        AppPaths(tmp_path / "app").job_dir(job_id)


@pytest.mark.parametrize("filename", ["", ".", "..", "../escape", "nested/../../escape", "/absolute", "C:\\escape",
                                      "C:escape", "\\\\host\\share", "..\\escape", "a:stream", "a\x00b"])
def test_job_paths_reject_path_escapes(tmp_path, filename):
    from h3server.paths import AppPaths

    with pytest.raises(ValueError):
        AppPaths(tmp_path / "app").job_path("job-123", filename)


@pytest.mark.parametrize("link_location", ["tmp", "tmp/jobs", "tmp/jobs/job-123", "tmp/jobs/job-123/output"])
def test_job_paths_reject_existing_symlink_escape(tmp_path, link_location):
    from h3server.paths import AppPaths

    paths = AppPaths(tmp_path / "app")
    outside = tmp_path / "outside"
    outside.mkdir()
    link = paths.root / link_location
    link.parent.mkdir(parents=True)
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        paths.job_path("job-123", "output/video.mp4")


def test_job_paths_reject_escape_to_another_job(tmp_path):
    from h3server.paths import AppPaths

    paths = AppPaths(tmp_path / "app")
    job = paths.job_dir("job-123")
    other = paths.job_dir("job-456")
    job.mkdir(parents=True)
    other.mkdir()
    (job / "output").symlink_to(other, target_is_directory=True)
    with pytest.raises(ValueError):
        paths.job_path("job-123", "output/video.mp4")


def test_cache_environment_rejects_existing_symlink_escape(tmp_path):
    from h3server.paths import AppPaths

    paths = AppPaths(tmp_path / "app")
    paths.root.mkdir()
    (paths.root / "cache").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        paths.cache_environment()


def test_import_is_inert_in_fresh_process(isolated_home):
    source = str(Path(__file__).resolve().parents[1] / "src")
    env = dict(os.environ, PYTHONPATH=source, PYTHONDONTWRITEBYTECODE="1")
    code = """
import os
import sys
before = dict(os.environ)
def guard(event, args):
    if event in {'os.mkdir', 'os.putenv', 'os.unsetenv'}:
        raise AssertionError(f'import had side effect: {event}')
sys.addaudithook(guard)
import h3server
import h3server.domain
import h3server.paths
assert dict(os.environ) == before
"""
    result = subprocess.run([sys.executable, "-c", code], env=env, cwd=isolated_home,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert list(isolated_home.iterdir()) == []


def test_job_directory_cannot_alias_another_job(tmp_path):
    from h3server.paths import AppPaths

    paths = AppPaths(tmp_path / "app")
    other = paths.job_dir("job-456")
    other.mkdir(parents=True)
    (paths.tmp_jobs / "job-123").symlink_to(other, target_is_directory=True)
    with pytest.raises(ValueError):
        paths.job_dir("job-123")


@pytest.mark.parametrize("name", ["NUL", "con", "COM1", "LPT9", "aux.txt", "file.", "file ", "a?b", "a*b"])
def test_job_paths_reject_windows_device_names_and_ambiguous_components(tmp_path, name):
    from h3server.paths import AppPaths

    with pytest.raises(ValueError):
        AppPaths(tmp_path / "app").job_path("job-123", f"nested/{name}")


@pytest.mark.parametrize("job_id", ["NUL", "con", "COM1", "LPT9"])
def test_job_identifiers_reject_windows_device_names(tmp_path, job_id):
    from h3server.paths import AppPaths

    with pytest.raises(ValueError):
        AppPaths(tmp_path / "app").job_dir(job_id)
