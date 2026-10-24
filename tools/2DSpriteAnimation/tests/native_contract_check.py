"""Explicit, offline conformance check against the real H3 server source.

Run with that source on PYTHONPATH and its existing test interpreter. Uses only
an isolated temporary SQLite/project/run store. No inference, sockets, credentials
or live-server mutations. Not part of the portable processor dependency set.
"""
import json
from pathlib import Path
import sys
import tempfile
from h3server.paths import AppPaths
from h3server.store import Store
from h3server.projects import Projects
from h3server.automation.runs import Runs
from h3server.automation.api import RunCreate, RunUpdate, Generate, Preflight
from h3server.remote.app import GenerationInput


def main():
    saved=json.loads(Path(sys.argv[1]).read_text())
    generation=saved.get('generation',saved)
    GenerationInput.model_validate(generation)
    assert 'num_frames' in generation and 'duration' not in generation
    digest='a'*64
    Preflight.model_validate({'generation':generation,'contract_digest':digest})
    with tempfile.TemporaryDirectory(prefix='sprite-native-contract-') as folder:
        paths=AppPaths(Path(folder))
        for directory in paths.directories():directory.mkdir(parents=True,exist_ok=True)
        store=Store(paths.state/'remote.sqlite')
        try:
            project=Projects(store).create('Sprite contract fixture')
            assert 'project_id' in project and 'id' not in project
            payload={'project_id':project['project_id'],'goal':'Check complete sprite metadata protocol','max_jobs':1,'max_revisions_per_shot':0}
            parsed=RunCreate.model_validate(payload)
            run=Runs(paths,store).create(**parsed.model_dump())
            assert 'id' in run and 'project_id' in run
            update=RunUpdate.model_validate({'revision':run['revision'],'patch':{'storyboard':[{'id':'animation','intent':payload['goal']}],'continuity':'Canonical style/source hashes','state':'active'}})
            run=Runs(paths,store).update(run['id'],update.revision,update.patch)
            Generate.model_validate({'generation':generation,'contract_digest':digest,'run_id':run['id'],'shot_id':'animation','request_id':'b'*32})
            assert not store.list_jobs()
            print(json.dumps({'real_native_project_run_generation_contracts_valid':True,'project_id_field':'project_id','run_id_field':'id','generation_timing':'num_frames_only','jobs_submitted':0}))
        finally:
            # Store opens/closes SQLite connections per transaction.
            assert not store.list_jobs()

if __name__=='__main__':main()
