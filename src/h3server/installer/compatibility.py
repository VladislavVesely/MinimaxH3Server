"""One audited predecessor, not a remote-code trust-on-first-use mechanism.

The locally built pre-diagnostics wheel was verified before rebuilding. Its exact
bytes preserve approved application installation and existing service receipts.
Only the CURRENT trusted worker executes diagnostics/retry. validate_plan checks
current schema, profile, pins, runtime sources and artifact contract; completed
privileged stages and service receipts are checked separately by Recovery.
"""
from importlib.resources import files
import hashlib

from h3server.installer.planner import ReleaseArtifact, validate_plan

PREVIOUS_SHA256 = 'd7e9af0db46e5256428a0bc1ff76fc9a56ee3209b21c74758ee5b92022f7dbba'
PREVIOUS_ARTIFACT = ReleaseArtifact('sha256-' + PREVIOUS_SHA256, 'h3server-0.1.0-py3-none-any.whl', PREVIOUS_SHA256)


def compatible_plan(plan):
    validate_plan(plan)
    if plan['artifact'] != vars(PREVIOUS_ARTIFACT) or plan['purpose'] != 'environment':
        raise ValueError('no audited compatibility proof for this release')


def previous_payload():
    data = files('h3server.installer').joinpath('compat', PREVIOUS_ARTIFACT.filename).read_bytes()
    if hashlib.sha256(data).hexdigest() != PREVIOUS_SHA256:
        raise ValueError('compatibility payload digest mismatch')
    return data
