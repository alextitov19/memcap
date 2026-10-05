"""Disposable environment ownership, cancellation and foreign resource controls."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'libexec'))
import environments as e


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / 'memcap'
        self.token = 'memcap-' + 'a'*32
        self.table = {'11': dict(start='owner', uid=os.getuid(), group=11)}
        self.row = dict(owner=dict(pid=11, start='owner'), agent=dict(pid=12, start='agent'),
                        phase='releasing', engine='unix:///fixture', claims=[], containers={'b'*64: 'created'})
        self.container = dict(Id='b'*64, Created='created', Config={'Labels': {'com.docker.compose.project': self.token}},
                              HostConfig={}, State={'Running': True})
        for target, value in [('environments.state_root', self.state), ('environments.local_engine', 'unix:///fixture'),
                              ('scheduler.processes', self.table), ('environments.containers', [self.container])]:
            stub = patch(target, return_value=value)
            stub.start()
            self.addCleanup(stub.stop)

    def save(self, **changes):
        with e.registry(self.state) as data:
            data[self.token] = {**self.row, **changes}

    def test_only_live_releasing_owner_may_stop_its_unique_containers(self):
        self.save()
        self.assertEqual(e.authorize(self.token, 11), ['b'*64])
        self.assertEqual(e.authorize(self.token, 99), [])
        self.save(phase='active')
        self.assertEqual(e.authorize(self.token, 11), [])

    def test_pin_claim_pause_and_engine_change_preserve_containers(self):
        for changes in [dict(pinned=True), dict(claims=[dict(pid=11, start='owner')]), dict(engine='unix:///other')]:
            self.save(**changes)
            self.assertEqual(e.authorize(self.token, 11), [])
        self.save()
        (self.state / 'paused').touch()
        self.assertEqual(e.authorize(self.token, 11), [])

    def test_foreign_label_auto_remove_and_recreated_container_are_not_owned(self):
        for changed in [dict(Config={'Labels': {}}), dict(HostConfig={'AutoRemove': True}), dict(Id='not-an-id')]:
            with self.assertRaises(ValueError):
                e.identities([{**self.container, **changed}], self.token)
        self.save(phase='active', orphan_since=0, observed=130)
        with patch('environments.time.time', return_value=130), patch('scheduler.processes', return_value={}), patch('environments.containers', return_value=[{**self.container, 'Created': 'new'}]):
            self.assertEqual(e.authorize(self.token), [])

    def test_orphan_cleanup_requires_both_owners_gone_and_continuous_grace(self):
        row = {**self.row, 'phase': 'active', 'orphan_since': 0, 'observed': 120}
        self.assertFalse(e.eligible(row, self.table, 120))
        self.assertFalse(e.eligible(row, {'12': dict(start='agent', uid=os.getuid())}, 120))
        self.assertTrue(e.eligible(row, {}, 120))
        self.assertFalse(e.eligible(row, {}, 211))
        self.assertFalse(e.eligible({**row, 'agent': {}}, {}, 120))
        self.assertTrue(e.eligible({**row, 'phase': 'releasing'}, {'12': dict(start='agent', uid=os.getuid())}, 120))

    def test_worker_cannot_forge_a_nested_lease(self):
        with patch('scheduler.Scheduler.nested', return_value=False), patch('environments.os.getpid', return_value=11):
            with self.assertRaisesRegex(ValueError, 'verified live'):
                e.worker('fixture.yml', ['true'], self.token)

    def test_environment_enters_queue_before_any_docker_operation(self):
        compose = Path(self.tmp.name) / 'compose.yaml'
        compose.write_text('services: {}')
        with patch('environments.os.execv', side_effect=RuntimeError('queued')) as launch, patch('environments.docker_json', side_effect=AssertionError('Docker before admission')):
            with self.assertRaisesRegex(RuntimeError, 'queued'):
                e.main(['run', '--memory', '12', '--compose', str(compose), '--', 'true'])
        self.assertIn('--memory', launch.call_args.args[1])
        self.assertIn('12.0', launch.call_args.args[1])

    def test_workflow_registers_before_start_and_releases_after_failed_test(self):
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            if 'start' in argv:
                with e.registry(self.state) as data:
                    self.assertEqual(data[self.token]['containers'], {'b'*64: 'created'})
                    self.assertEqual(data[self.token]['phase'], 'prepared')
            if argv == ['fixture-test']:
                self.assertEqual(kwargs['env']['COMPOSE_PROJECT_NAME'], self.token)
            return Mock(returncode=9 if argv == ['fixture-test'] else 0)
        with patch('scheduler.Scheduler.nested', return_value=True), patch('environments.os.getpid', return_value=11), patch('orphan_recovery.agent_identity', return_value={'agent_owner': 12, 'agent_start': 'agent'}), patch('environments.containers', side_effect=[[], [self.container], [self.container]]):
            self.assertEqual(e.worker('fixture.yml', ['fixture-test'], self.token, runner=run), 9)
        self.assertIn('--no-start', calls[0])
        self.assertEqual(calls[-1], ['/bin/bash', str(e.ROOT / 'bin/memcap'), '_environment-stop', self.token])

    def test_outer_small_job_cannot_underreserve_nested_environment(self):
        data = dict(jobs=[dict(id='lease', status='running', memory_kb=1048576)])
        self.assertFalse(e.adequate_budget(data, 'lease', 12 * 1048576))
        self.assertFalse(e.adequate_budget(data, 'forged', 1048576))
        self.assertTrue(e.adequate_budget(data, 'lease', 1048576))


if __name__ == '__main__':
    unittest.main()
