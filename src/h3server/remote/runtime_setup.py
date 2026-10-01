"""Provision additional model environments without replacing the daemon/service.

System package/reboot actions stay with the authorized controller coordinator;
this executor only enters the unprivileged environment stages on a ready host.
"""
from dataclasses import asdict
from pathlib import Path
import os

from h3server.backends.base import atomic_json, read_json, directory
from h3server.installer.planner import ReleaseArtifact, build_plan, inspect_prerequisites
from h3server.installer.steps import Steps, ProcessIO, artifact_path
from h3server.installer.diagnostics import safe_log
from h3server.store import canonical_plan_digest


class Progress:
    def __init__(self, publish, stop):self.publish,self.stop=publish,stop
    def phase(self, stage, substage, label):
        if self.stop.is_set():raise InterruptedError('Runtime setup stopped; completed work retained')
        self.publish(state='runtime_'+substage,runtime_message=safe_log(label)[:1000])
    def write(self, text):
        self.publish(runtime_message=safe_log(text)[-2000:])
    def flush(self):pass


class RuntimeSetup:
    def __init__(self, paths, bindings):self.paths,self.bindings=paths,bindings

    def ensure(self, profile, artifact, maintenance_fd, stop, publish):
        existing=self.bindings.resolve(profile)
        if existing:return existing
        # Reuse only a verified family, with a new native probe for this profile.
        for other,binding in self.bindings._bindings().items():
            if self.bindings.family(other)==self.bindings.family(profile):
                candidate=self.bindings.resolve(other)
                return self.bindings.publish(profile,candidate['environment'],artifact=candidate['artifact'],maintenance_fd=maintenance_fd)
        if artifact is None:
            try:artifact=read_json(self.paths,'config/runtime-release.json')
            except FileNotFoundError:raise ValueError('A verified controller release must be staged before runtime setup') from None
        artifact=ReleaseArtifact(**artifact)
        publish(state='runtime_planning',completed=0,runtime_message='Inspecting runtime prerequisites')
        plan=build_plan(inspect_prerequisites(),profile=profile,artifact=artifact)
        if plan['blockers']:raise ValueError('; '.join(plan['blockers']))
        if plan['purpose']!='environment' or plan['privileged_required'] or plan['reboot_required']:
            raise ValueError('System prerequisites require authorized setup before adding this runtime; no privileged operation was attempted')
        artifact_path(self.paths,plan)
        digest=canonical_plan_digest(plan)
        marker=f'state/runtime-provisions/{digest}.json'
        try:
            if read_json(self.paths,marker)!=plan:raise ValueError('Runtime provisioning plan changed')
        except FileNotFoundError:
            if (self.paths.environments/digest).exists():raise ValueError('Refusing to modify an unbound existing environment')
            atomic_json(self.paths,marker,plan)
        with directory(self.paths,'environments/'+digest,create=True) as fd:
            info=os.fstat(fd)
            if info.st_uid!=os.getuid() or info.st_mode & 0o022:raise ValueError('Unsafe runtime environment owner or permissions')
        io=ProcessIO(command_timeout=3600);io.lock_fd=maintenance_fd
        log=Progress(publish,stop)
        steps=Steps(self.paths,plan,digest,io,log)
        for stage in ('environment','dependencies','verify'):
            if stop.is_set():raise InterruptedError('Runtime setup stopped; completed work retained')
            publish(state='runtime_'+stage,completed=0,runtime_message='Preparing '+profile+': '+stage)
            getattr(steps,stage)()
        if stop.is_set():raise InterruptedError('Runtime setup stopped before publication')
        return self.bindings.publish(profile,digest,artifact=asdict(artifact),maintenance_fd=maintenance_fd)
