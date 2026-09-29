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
        self._cancel = threading.Event()
        self._requests = {}
        self._connection_status = {'state': 'disconnected'}
        self._status = {'state': 'idle', 'phase': 'idle', 'operation_id': None, 'error': None,
                        'installation_ready': False, 'model_ready': False, 'generation_tested': False}
        recovery.observation_allowed = lambda: not self.active()

    def active(self):
        with self._lock:
            return self._worker is not None and self._worker.is_alive()

    def status(self):
        with self._lock:
            return {**deepcopy(self._status), 'busy': self.active()}

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
            self._status.update(values)

    def start(self, profile, artifact_set_digest, request_id):
        # Serialized with explicit controller operations, but never with worker IO.
        with self.recovery.operations, self._lock:
            selection = profile_document(profile)
            if selection['artifact_set_digest'] != artifact_set_digest:
                raise ControllerError('selection_changed')
            binding = (profile, artifact_set_digest)
            request_binding = (self.recovery.target(), binding)
            if request_id in self._requests:
                prior_binding, prior_status = self._requests[request_id]
                if request_binding != prior_binding:
                    raise ControllerError('setup_request_conflict')
                return {**deepcopy(prior_status), 'busy': prior_status is self._status and self.active()}
            if self.active() or self.recovery.active():
                raise ControllerError('setup_in_progress')
            if len(self._requests) >= 32:
                raise ControllerError('setup_session_limit')
            target = self.recovery.setup_target()
            if self._observe_connection()['state'] != 'authenticated':
                raise ControllerError('authentication_required')
            if self.deployment.release is None:
                raise ControllerError('release_unavailable')
            self._cancel = threading.Event()
            self._status = {'state': 'running', 'phase': 'planning', 'operation_id': uuid.uuid4().hex,
                            'request_id': request_id, 'profile': profile, 'artifact_set_digest': artifact_set_digest,
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
            self._status = {'state': 'idle', 'phase': 'idle', 'operation_id': None, 'error': None,
                            'installation_ready': False, 'model_ready': False, 'generation_tested': False}

    def cancel(self):
        # Do not join here: status/cancel/auth must not wait for package installation.
        with self._lock:
            self._cancel.set()
            self.recovery.revoke()
            if self.active():
                self._status.update(state='cancelling')
                if self._status['phase'] != 'downloading':
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
                or document['plan']['artifact'] != asdict(self._release)
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
        self._publish(snapshot={**snapshot, 'events': self._events})
        return snapshot

    def _action(self, action, **values):
        document = self._binding()
        return self._call(lambda: self.deployment.action(action, {
            'plan_digest': document['plan_digest'], **values}), mutation=True)

    def _initial_plan(self):
        if self.deployment.plan is None:
            try:
                self._call(lambda: self.deployment.restore(observation_only=True))
            except FileNotFoundError:
                pass
        if self.deployment.plan is not None:
            prior = self._call(self.deployment.snapshot)
            operation = prior.get('operation')
            if operation and operation['state'] in {'running', 'interrupted'}:
                raise ControllerError('setup_remote_busy')
            if self.deployment.plan['profile'] == self._selection[0]:
                return self._binding()
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
            self._root = self.deployment.root
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
                    # Resume only unprivileged environment stages with durable receipt.
                    if plan['purpose'] != 'environment' or digest in submitted:
                        raise ControllerError('setup_install_failed')
                    if digest not in resumed:
                        resumed[digest] = time.monotonic()
                        self._publish(phase='installing')
                        self._action('resume')
                    elif time.monotonic() - resumed[digest] > 30:
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
            self._publish(state='completed', phase='completed', model_ready=True)
        except Exception as error:
            safe = {'selection_changed', 'invalid_checkpoint', 'setup_remote_busy', 'setup_replan_limit', 'setup_plan_blocked',
                    'setup_replan_stalled', 'setup_install_failed', 'setup_operation_outcome_unknown',
                    'setup_reboot_not_observed', 'setup_activation_failed', 'setup_operation_failed',
                    'setup_timeout', 'setup_cancelled', 'setup_sudo_password_rejected',
                    'setup_credential_check_failed', 'setup_credential_required', 'setup_reauthentication_required',
                    'authentication_failed', 'host_key_changed', 'trust_required', 'reconnect_timeout',
                    'recovery_binding_changed', 'matching_release_required', 'update_prerequisite_maintenance_required'}
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
            self.recovery.revoke()

    def _await_operation(self, action, digest, expected_operation=None):
        deadline = time.monotonic() + 30
        while True:
            snapshot = self._snapshot()
            operation = snapshot.get('operation')
            if operation and operation['action'] == action and operation['plan_digest'] == digest:
                if expected_operation is not None and operation['operation'] != expected_operation:
                    raise ControllerError('setup_remote_busy')
                if operation['state'] == 'completed':
                    return snapshot
                if operation['state'] in {'failed', 'cancelled', 'interrupted'}:
                    raise ControllerError('setup_operation_failed')
                if operation['state'] != 'running':
                    raise ControllerError('invalid_checkpoint')
            elif time.monotonic() > deadline:
                raise ControllerError('setup_operation_outcome_unknown')
            self._wait()
