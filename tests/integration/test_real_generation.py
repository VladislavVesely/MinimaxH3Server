"""Opt-in Linux GPU acceptance. No download, installation, SSH or hosted fallback.

Run only on an approved local inference target with the pinned environment and
verified snapshot already installed, persisted license/eligibility/download
consent, and sufficient resource budget:
 H3_REAL_GENERATION=I_APPROVE_LOCAL_H3_GENERATION
 H3_REAL_GENERATION_ROOT=/absolute/managed/root
 H3_REAL_GENERATION_PROFILE=h3-bf16-cpu-offload-experimental (or int8 profile)
 pytest -s tests/integration/test_real_generation.py

This executes the real Task9 JSONL child contract, validates decoded video AND
audio, retains the generated MP4 under tmp/jobs, and prints exact revisions,
installed versions, effective duration and its path. Missing requirements FAIL
when opted in; they are never replaced with fixture generation or network fetches.
"""
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

from h3server.backends import load_manifest
from h3server.backends.base import atomic_json
from h3server.backends.h3 import validate_media
from h3server.domain import GenerationSettings, H3_PROFILES, JobRequest
from h3server.downloads import require_consent
from h3server.events import decode_event
from h3server.paths import AppPaths


def test_real_local_h3_produces_playable_mp4_with_audio():
    if os.environ.get('H3_REAL_GENERATION') != 'I_APPROVE_LOCAL_H3_GENERATION':
        pytest.skip('real GPU generation not authorized; boundary stubs are not inference acceptance')
    assert sys.platform.startswith('linux'), 'run on the approved Linux inference target'
    root = os.environ.get('H3_REAL_GENERATION_ROOT')
    assert root and Path(root).is_absolute(), 'explicit managed target root required'
    paths = AppPaths(root)
    require_consent(paths)
    profile = os.environ.get('H3_REAL_GENERATION_PROFILE', H3_PROFILES[0])
    settings = GenerationSettings(profile=profile)
    env = {key: value for key, value in os.environ.items() if key in ('PATH','LANG','LD_LIBRARY_PATH','CUDA_VISIBLE_DEVICES')}
    env.update(paths.cache_environment())
    env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    result = subprocess.run([sys.executable, '-m', 'h3server.backends.h3', '--diagnose', '--root', str(paths.root)],
                            env=env, capture_output=True, timeout=1800, check=True)
    diagnostic = json.loads(result.stdout)
    assert diagnostic['environment_ready'] and diagnostic['model_ready'], diagnostic
    assert not diagnostic['generation_tested'] and not diagnostic['model_loaded']
    job_id = uuid.uuid4().hex
    directory = paths.job_dir(job_id)
    directory.mkdir(mode=0o700)
    output = directory / 'video.mp4'
    request = JobRequest('A red fox trotting through a snowy pine forest, snow crunching underfoot', settings=settings)
    atomic_json(paths, f'tmp/jobs/{job_id}/request.json', {'schema_version':1, 'job_id':job_id,
                'root':str(paths.root), 'output_path':str(output), 'request':asdict(request)})
    result = subprocess.run([sys.executable, '-m', 'h3server.backends.h3', '--request', str(directory / 'request.json')],
                            env=env, capture_output=True, timeout=6*3600, check=True)
    events = [decode_event(line) for line in result.stdout.decode().splitlines()]
    denoising = [e for e in events if e.stage == 'denoising']
    assert denoising and denoising[-1].status == 'completed'
    assert denoising[-1].progress.completed == denoising[-1].progress.total
    assert events[-1].stage == 'validation' and events[-1].status == 'completed'
    validate_media(output, settings)
    (directory / 'request.json').unlink()
    print(json.dumps({'generation_tested': True, 'diagnostic': diagnostic, 'profile': profile,
                      'model_revision':load_manifest()['model']['revision'], 'settings':asdict(settings),
                      'effective_duration':settings.duration, 'playable_mp4':str(output)}, indent=2))
