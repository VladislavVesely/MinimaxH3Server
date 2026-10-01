"""Bounded composite Install intent over the existing digest-bound deployment API.

Only this source-owned workflow supplies approvals. No commands or busy-job policy
come from the browser. Status is a memory snapshot, not a blocking remote poll.
Remote checkpoints remain authoritative; lost mutation replies are never retried.
"""
from copy import deepcopy
from dataclasses import asdict
import threading
import time
import uuid

from h3server.controller.deploy import profile_document
from h3server.controller.ssh import ControllerError
from h3server.installer.planner import Approval
from h3server.store import canonical_plan_digest


class SetupCoordinator:
    def __init__(self, deployment, recovery, session_active, *, timeout=86400, poll_interval=.5):
        self.deployment, self.recovery = deployment, recovery
        self.connection = deployment.connection
        self.session_active = session_active
        self.timeout, self.poll_interval = timeout, poll_interval
        self._lock = threading.RLock()
        self._worker = None
        self._phase_started = self._phase_ended = None
        self._cancel = threading.Event()
        self._requests = {}
        self._desired = None
        self._component_operation = None
        self._connection_status = {'state': 'disconnected'}
        self._status = {'state': 'idle', 'phase': 'idle', 'operation_id': None, 'error': None,
                        'installation_ready': False, 'model_ready': False, 'generation_tested': False}
        recovery.observation_allowed = lambda: not self.active()

    def active(self):
        with self._lock:
            return self._worker is not None and self._worker.is_alive()

    def status(self):
        with self._lock:
            elapsed = None if self._phase_started is None else max(0, (
                self._phase_ended if self._phase_ended is not None else time.monotonic()) - self._phase_started)
            return {**deepcopy(self._status), 'busy': self.active(), 'phase_elapsed_seconds': elapsed}

    def connection_status(self):
        """Last observed SSH state; never wait on the worker's SSH lock."""
        with self._lock:
            return dict(self._connection_status)

    def _observe_connection(self):
        state = self.connection.status()
        with self._lock:
            self._connection_status = state
        return state

    def _publish(self, **values):
        with self._lock:
            if 'phase' in values and values['phase'] != self._status['phase']:
                self._phase_started, self._phase_ended = time.monotonic(), None
            if values.get('state') in {'completed', 'failed', 'cancelled'} and self._phase_ended is None:
                self._phase_ended = time.monotonic()
            self._status.update(values)

    def start(self, profile, artifact_set_digest, request_id, *, retry_digest=None, sage_attention=False, turbo_lora=False, sparse_attention=False, desired=None):
        # Serialized with explicit controller operations, but never with worker IO.
        with self.recovery.operations, self._lock:
            selection = profile_document(profile)
            if sage_attention and not selection['runtime_id'].startswith('comfyui-h3-'):
                raise ControllerError('optional_install_busy_or_unsupported')
            from h3server.domain import TURBO_PROFILES
            if turbo_lora and profile not in TURBO_PROFILES:
                raise ControllerError('optional_install_busy_or_unsupported')
            if selection['artifact_set_digest'] != artifact_set_digest:
                raise ControllerError('selection_changed')
            binding = (profile, artifact_set_digest)
            if desired is not None:
                from h3server.remote.model_catalog import MODEL_NAMES
                allowed=set(MODEL_NAMES)|{'sageattention','turbo','sparse','reference'}
                if not isinstance(desired,list) or len(desired)!=len(set(desired)) or set(desired)-allowed or profile not in desired:
                    raise ControllerError('selection_changed')
            if sparse_attention and profile != 'h3-convrot-int8':
                raise ControllerError('optional_install_busy_or_unsupported')
            request_binding = (self.recovery.target(), binding, retry_digest, sage_attention, turbo_lora, sparse_attention, tuple(sorted(desired)) if desired is not None else None)
            if request_id in self._requests:
                prior_binding, prior_status = self._requests[request_id]
                if request_binding != prior_binding:
                    raise ControllerError('setup_request_conflict')
                return {**deepcopy(prior_status), 'busy': prior_status is self._status and self.active()}
            if self.active() or self.recovery.active():
                raise ControllerError('setup_in_progress')
            if len(self._requests) >= 32:
                raise ControllerError('setup_session_limit')
            target = self.recovery.setup_target() if retry_digest is None else self.recovery.target()
            self._retry_digest = retry_digest
            self._desired = desired
            self._component_operation = None
            self._sage_requested = sage_attention
            self._turbo_requested = turbo_lora
            self._sparse_requested = sparse_attention
            if self._observe_connection()['state'] != 'authenticated':
                raise ControllerError('authentication_required')
            if self.deployment.release is None:
                raise ControllerError('release_unavailable')
            self._cancel = threading.Event()
            self._phase_started, self._phase_ended = time.monotonic(), None
            self._status = {'state': 'running', 'phase': 'planning', 'operation_id': uuid.uuid4().hex,
                            'request_id': request_id, 'profile': profile, 'artifact_set_digest': artifact_set_digest,
                            'sage_attention': sage_attention, 'turbo_lora': turbo_lora, 'sparse_attention': sparse_attention, 'desired':desired,
                            'plan_digest': None, 'error': None, 'snapshot': None,
                            'installation_ready': False, 'model_ready': False, 'generation_tested': False}
            self._requests[request_id] = (request_binding, self._status)
            self._worker = threading.Thread(target=self._run, args=(target, binding),
                                            name='h3-one-click-setup', daemon=True)
            self._worker.start()
            return self.status()

    def reset(self):
        """Forget visible progress when the explicit target is replaced/disconnected."""
        with self._lock:
            if self.active():
                raise ControllerError('setup_in_progress')
            self._phase_started = self._phase_ended = None
            self._status = {'state': 'idle', 'phase': 'idle', 'operation_id': None, 'error': None,
                            'installation_ready': False, 'model_ready': False, 'generation_tested': False}

    def cancel(self):
        # Do not join here: status/cancel/auth must not wait for package installation.
        with self._lock:
            self._cancel.set()
            self.recovery.revoke()
            if self.active():
                self._status.update(state='cancelling')
                if self._status['phase'] not in ('downloading','components'):
                    self.connection.interrupt_setup()
                    self._connection_status = {'state': 'disconnected'}
            return self.status()

    def expire(self):
        if not self.session_active():
            self.cancel()

    def close(self):
        self.cancel()
        worker = self._worker
        if worker is not None:
            worker.join()

    def _check(self):
        if self._cancel.is_set() or not self.session_active():
            raise ControllerError('setup_cancelled')
        if time.monotonic() >= self._deadline:
            raise ControllerError('setup_timeout')
        # Every operation is bound to the consenting authenticated target/session.
        if self._retry_digest is not None:
            if self.recovery.target() != self._target:
                raise ControllerError('recovery_binding_changed')
            if self.connection.status()['state'] != 'authenticated':
                raise ControllerError('setup_reauthentication_required')
            return
        try:
            self.recovery.setup_password(self._target)
        except ControllerError:
            status = self.recovery.status()
            if status['state'] == 'failed' and status['error']:
                raise ControllerError(status['error']) from None
            raise

    def _wait(self):
        self._cancel.wait(self.poll_interval)
        self._check()

    def _recover(self):
        self._check()
        self._publish(phase='reconnecting')
        with self._lock:
            self._connection_status = {'state': 'disconnected'}
        if not self.recovery.active() and not self.recovery.start():
            raise ControllerError(self.recovery.status()['error'] or 'setup_reauthentication_required')
        while self.recovery.active():
            self._wait()
        status = self.recovery.status()
        if status['state'] != 'recovered':
            raise ControllerError(status['error'] or 'setup_recovery_failed')
        self._observe_connection()
        self._binding()

    def _call(self, function, *, mutation=False):
        self._check()
        if self._observe_connection()['state'] == 'disconnected':
            self._recover()
        try:
            result = function()
        except Exception:
            if self._observe_connection()['state'] != 'disconnected':
                raise
            self._recover()
            if mutation:
                return None  # ambiguous: inspect checkpoint, NEVER replay mutation
            result = function()  # one bounded observation retry only
        self._observe_connection()
        if not mutation:
            self._check()
        return result

    def _binding(self):
        document = self.deployment.document()
        if ((document['plan']['profile'], document['selection']['artifact_set_digest']) != self._selection
                or self.deployment.release.artifact != self._release
                or (document['plan']['artifact'] != asdict(self._release) and not self.deployment.compatible)
                or (self._root is not None and self.deployment.root != self._root)):
            raise ControllerError('selection_changed')
        if document['plan'].get('artifact_set_digest') != self._selection[1]:
            raise ControllerError('selection_changed')
        self._publish(plan_digest=document['plan_digest'])
        return document

    def _snapshot(self):
        snapshot = self._call(lambda: self.deployment.snapshot(self._cursor))
        self._binding()
        self._cursor = snapshot['cursor']
        self._events = (self._events + snapshot['events'])[-100:]
        self._publish(snapshot={**snapshot, 'events': self._events},
                      diagnostics=snapshot.get('diagnostics'), retry=snapshot.get('retry'))
        return snapshot

    def _action(self, action, **values):
        document = self._binding()
        return self._call(lambda: self.deployment.action(action, {
            'plan_digest': document['plan_digest'], **values}), mutation=True)

    def _initial_plan(self):
        if self._retry_digest is None:
            additional = self._call(lambda: self.deployment.prepare_additional_setup(self._selection[0]), mutation=True)
            if additional is not None:return additional
        compatible = self._call(self.deployment.restore_compatible_failure)
        if compatible is not None:
            return compatible
        if self._retry_digest is not None:
            if self.deployment.plan is None:
                self._call(lambda: self.deployment.restore(observation_only=True))
            if canonical_plan_digest(self.deployment.plan) != self._retry_digest:
                raise ControllerError('selection_changed')
            return self._binding()
        fresh = self._call(lambda: self.deployment.rediscover_stale_plan(self._selection[0]), mutation=True)
        if fresh is not None:
            return fresh
        if self.deployment.plan is None:
            try:
                self._call(lambda: self.deployment.restore(observation_only=True))
            except ControllerError as error:
                if error.code != 'setup_not_found':
                    raise
        if self.deployment.plan is not None:
            prior = self._call(self.deployment.snapshot)
            operation = prior.get('operation')
            if operation and operation['state'] in {'running', 'interrupted'}:
                raise ControllerError('setup_remote_busy')
            if self.deployment.plan['profile'] == self._selection[0]:
                document = self._binding()
                if not self._call(lambda: self.deployment.can_rediscover_blocked_plan(prior)):
                    return document
            if any(s['state'] in {'running', 'waiting_for_reboot'} for s in prior['setups']):
                raise ControllerError('setup_remote_busy')
        return self._call(lambda: self.deployment.make_plan(self._selection[0], sudo_password_available=True))

    def _run(self, target, selection):
        self._target, self._selection = target, selection
        self._release = self.deployment.release.artifact
        self._deadline = time.monotonic() + self.timeout
        submitted, resumed, rebooted = {}, {}, {}
        self._root = None
        self._cursor, self._events = 0, []
        rounds, checked_password = 0, False
        self._download_operation = None
        try:
            self._initial_plan()
            if self._retry_digest is not None and canonical_plan_digest(self.deployment.plan) != self._retry_digest:
                raise ControllerError('selection_changed')
            self._root = self.deployment.root
            if self._desired is not None and set(self._desired)!={selection[0]}:
                self._call(lambda:self.deployment.stage_component_intent(self._desired,self._status['request_id']),mutation=True)
            if self._retry_digest is not None and not self._snapshot().get('retry', {}).get('eligible'):
                raise ControllerError('setup_retry_not_safe')
            while True:
                document = self._binding()
                digest, plan = document['plan_digest'], document['plan']
                if plan['blockers']:
                    self._publish(blockers=plan['blockers'])
                    raise ControllerError('setup_plan_blocked')
                snapshot = self._snapshot()
                checkpoints = [s for s in snapshot['setups'] if s['plan_digest'] == digest]
                if len(checkpoints) > 1:
                    raise ControllerError('invalid_checkpoint')
                state = checkpoints[0]['state'] if checkpoints else 'planned'
                if state == 'waiting_for_replan':
                    rounds += 1
                    if rounds > 4:
                        raise ControllerError('setup_replan_limit')
                    self._publish(phase='replanning')
                    self._call(lambda: self.deployment.make_plan(selection[0], sudo_password_available=True))
                    if canonical_plan_digest(self.deployment.plan) == digest:
                        raise ControllerError('setup_replan_stalled')
                    continue
                if state == 'ready':
                    break
                if state == 'failed':
                    if digest in resumed:
                        operation = snapshot.get('operation')
                        if (operation and operation.get('action') == 'retry-stage' and operation.get('state') in {'failed', 'interrupted'}) or time.monotonic() - resumed[digest] > 30:
                            raise ControllerError('setup_install_failed')
                    elif self._retry_digest != digest or not snapshot.get('retry', {}).get('eligible') or digest in submitted:
                        raise ControllerError('setup_install_failed')
                    else:
                        resumed[digest] = time.monotonic()
                        self._publish(phase='installing')
                        setup_id = checkpoints[0].get('setup_id')
                        if not setup_id:
                            raise ControllerError('invalid_checkpoint')
                        result = self._action('retry-stage')
                        operation_id = result.get('operation') if result else None
                        if not isinstance(operation_id, str) or not operation_id:
                            raise ControllerError('setup_operation_outcome_unknown')
                        completed = self._await_operation('retry-stage', digest, operation_id,
                            expected_setup_id=setup_id)
                        if completed['setups'][0]['state'] != 'ready':
                            raise ControllerError('setup_install_failed')
                elif state == 'waiting_for_reboot':
                    self._publish(phase='rebooting')
                    if digest not in rebooted:
                        if not checked_password:
                            self._call(lambda: self.connection.check_setup_password(self.recovery.setup_password(target)))
                            checked_password = True
                        rebooted[digest] = time.monotonic()
                        approval = Approval(digest, True, True, True, True)
                        result = self._action('reboot', **{k: v for k, v in asdict(approval).items() if k != 'plan_digest'},
                                     sudo_password=self.recovery.setup_password(target))
                        if result is not None:
                            self._recover()
                    elif time.monotonic() - rebooted[digest] > 180:
                        raise ControllerError('setup_reboot_not_observed')
                elif state in {'planned', 'awaiting_approval'}:
                    if digest in submitted:
                        if time.monotonic() - submitted[digest] > 30:
                            raise ControllerError('setup_operation_outcome_unknown')
                        self._wait()
                        continue
                    if not checked_password:
                        self._call(lambda: self.connection.check_setup_password(self.recovery.setup_password(target)))
                        checked_password = True
                    if plan['purpose'] == 'environment' and document['service'] is None:
                        # Read-only inspection deliberately omits service-plan IO.
                        # Load it only for explicit Install, before exact consent
                        # and the durable at-most-once launch marker are checked.
                        self._call(lambda: self.deployment.restore(expected_digest=digest,
                            expected_root=self._root, observation_only=True))
                        document = self._binding()
                    submitted[digest] = time.monotonic()
                    self._publish(phase='prerequisites' if plan['purpose'] == 'prerequisites' else 'installing')
                    self._call(lambda: self.deployment.install_once(Approval(digest, True, True, True, True),
                        service_digest=document['service_digest'], sudo_password=self.recovery.setup_password(target)), mutation=True)
                elif state not in {'running', 'reconnecting'}:
                    raise ControllerError('invalid_checkpoint')
                else:
                    self._publish(phase='prerequisites' if plan['purpose'] == 'prerequisites' else 'installing')
                self._wait()
            self._publish(installation_ready=True)
            active = snapshot.get('active_release')
            if not active or active['environment'] != digest:
                self._publish(phase='activating')
                result = self._action('activate', activation_approved=True, policy='reject', cancel_confirmed=False)
                snapshot = self._await_operation('activate', digest, result.get('operation') if result else None)
                if not snapshot.get('active_release') or snapshot['active_release']['environment'] != digest:
                    raise ControllerError('setup_activation_failed')
            operation = snapshot.get('operation')
            if not (operation and operation['plan_digest'] == digest and operation['action'] == 'download'
                    and operation['state'] == 'completed'):
                self._publish(phase='downloading')
                result = self._action('download', license_sha256=document['license_sha256'],
                    artifact_set_digest=selection[1], license_acknowledged=True,
                    eligibility_confirmed=True, download_approved=True)
                if result:
                    self._download_operation = result.get('operation')
                self._await_operation('download', digest, self._download_operation)
            self._check()
            self._publish(model_ready=True)
            if self._sage_requested:
                self._publish(phase='sage_attention')
                feature = self._call(lambda: self.sage_request('POST'), mutation=True)
                while True:
                    self._publish(sage_install=feature)
                    if feature.get('installed') and not feature.get('busy'):
                        break
                    if feature.get('state') == 'failed':
                        raise ControllerError('sage_install_failed')
                    self._wait()
                    feature = self._call(lambda: self.sage_request('GET'))
            if self._sparse_requested:
                self._publish(phase='sparse_attention')
                feature = self._call(lambda: self.sparse_request('POST'), mutation=True)
                while True:
                    self._publish(sparse_install=feature)
                    if feature.get('installed') and not feature.get('busy'): break
                    if feature.get('state') == 'failed': raise ControllerError('sparse_install_failed')
                    self._wait()
                    feature = self._call(lambda: self.sparse_request('GET'))
            if self._turbo_requested:
                self._publish(phase='turbo_lora')
                feature = self._call(lambda: self.turbo_request('POST'), mutation=True)
                while True:
                    self._publish(turbo_install=feature)
                    if feature.get('installed') and not feature.get('busy'): break
                    if feature.get('state') == 'failed': raise ControllerError('turbo_install_failed')
                    self._wait()
                    feature = self._call(lambda: self.turbo_request('GET'))
            if self._desired is not None and set(self._desired)!={selection[0]}:self._complete_components()
            self._publish(state='completed', phase='completed', model_ready=True)
        except Exception as error:
            safe = {'selection_changed', 'invalid_checkpoint', 'setup_remote_busy', 'setup_replan_limit', 'setup_plan_blocked',
                    'setup_replan_stalled', 'setup_install_failed', 'setup_operation_outcome_unknown',
                    'setup_reboot_not_observed', 'setup_activation_failed', 'setup_operation_failed',
                    'setup_timeout', 'setup_cancelled', 'setup_sudo_password_rejected',
                    'setup_credential_check_failed', 'setup_credential_required', 'setup_reauthentication_required',
                    'authentication_failed', 'host_key_changed', 'trust_required', 'reconnect_timeout',
                    'recovery_binding_changed', 'matching_release_required', 'update_prerequisite_maintenance_required',
                    'stale_discovery_unproven', 'staging_lock_unavailable', 'staging_lock_lost',
                    'compatible_retry_not_safe', 'compatible_retry_contract_changed', 'setup_retry_not_safe',
                    'component_sync_failed', 'sage_install_failed', 'turbo_install_failed', 'turbo_selection_changed', 'sparse_install_failed', 'sparse_selection_changed', 'optional_install_busy_or_unsupported'}
            code = error.code if isinstance(error, ControllerError) and error.code in safe else 'setup_failed'
            cancelled = self._cancel.is_set() or not self.session_active()
            self._publish(state='cancelled' if cancelled else 'failed', error=None if cancelled else code)
        finally:
            # Cancellation does not kill package managers or unrelated jobs. A download
            # can be cancelled only by its exact owned operation ID on the target.
            if self._cancel.is_set() and self._download_operation is not None:
                try:
                    self.deployment.action('cancel-download', {'plan_digest': self._status['plan_digest'],
                                            'operation': self._download_operation})
                except Exception:
                    pass  # may be offline; no cancellation of another operation
            if self._cancel.is_set() and self._component_operation is not None:
                try:self.component_request('CANCEL',{'operation':self._component_operation})
                except Exception:pass
            self.recovery.revoke()

    def _complete_components(self):
        from h3server.backends import load_manifest
        self._publish(phase='components')
        library=self._call(lambda:self.component_request('GET'))
        inventory=library.get('components',{})
        if not inventory.get('supported'):raise ControllerError('component_sync_failed')
        payload=dict(action='synchronize',desired=self._desired,inventory_digest=inventory['digest'],
            license_sha256=load_manifest()['sources']['license']['sha256'],license_acknowledged=True,
            eligibility_confirmed=True,download_approved=True)
        result=self._call(lambda:self.component_request('POST',payload),mutation=True)
        self._component_operation=result['operation']
        while True:
            self._publish(component_sync=result)
            if not result.get('busy'):
                if result.get('state')!='completed':raise ControllerError('component_sync_failed')
                return
            self._wait()
            result=self._call(lambda:self.component_request('GET'))['synchronization']
            if result.get('operation')!=self._component_operation:raise ControllerError('component_sync_failed')

    def _await_operation(self, action, digest, expected_operation=None, *, expected_setup_id=None):
        deadline = time.monotonic() + 30
        release_deadline = None
        while True:
            snapshot = self._snapshot()
            if expected_setup_id is not None:
                checkpoints = snapshot.get('setups', [])
                if (len(checkpoints) != 1 or checkpoints[0].get('setup_id') != expected_setup_id
                        or checkpoints[0].get('plan_digest') != digest):
                    raise ControllerError('invalid_checkpoint')
            operation = snapshot.get('operation')
            if expected_setup_id is not None and operation and operation.get('operation') != expected_operation:
                raise ControllerError('setup_remote_busy')
            if operation and operation['action'] == action and operation['plan_digest'] == digest:
                if expected_operation is not None and operation['operation'] != expected_operation:
                    raise ControllerError('setup_remote_busy')
                if operation['state'] == 'completed':
                    if expected_setup_id is None or operation.get('lock_released') is True:
                        return snapshot
                    # READY and even the terminal record precede the detached
                    # worker's lock release. Missing metadata is not proof.
                    if release_deadline is None:
                        release_deadline = time.monotonic() + 30
                    if time.monotonic() > release_deadline:
                        raise ControllerError('setup_operation_outcome_unknown')
                    self._wait()
                    continue
                if operation['state'] in {'failed', 'cancelled', 'interrupted'}:
                    raise ControllerError('setup_operation_failed')
                if operation['state'] != 'running':
                    raise ControllerError('invalid_checkpoint')
            elif time.monotonic() > deadline:
                raise ControllerError('setup_operation_outcome_unknown')
            self._wait()
