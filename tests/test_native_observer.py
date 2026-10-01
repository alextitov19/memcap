"""Native observation cannot reserve memory, signal work or certify a low peak."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'libexec'))
import native_observer as native


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'native'
        native.private(self.root)
        self.fingerprint = 'a' * 64
        self.path = self.root / 'active' / 'entry.json'
        native.write_json(self.path, dict(pid=10, identity=100, fingerprint=self.fingerprint,
                                          created=1000, session='b' * 64))
        self.table = {10: dict(ppid=1, uid=os.getuid()), 11: dict(ppid=10, uid=os.getuid())}
        self.samples = {10: dict(identity=100, footprint_kb=100),
                        11: dict(identity=110, footprint_kb=native.THRESHOLD_KB)}
        self.observer = native.Observer(self.root, self.samples.get, lambda: self.table)

    def test_owned_descendant_promotes_future_exact_work_without_queue_state(self):
        self.assertEqual(self.observer.tick(1002), [])
        profile = native.read_json(self.root / 'profiles.json')[self.fingerprint]
        self.assertEqual(profile['peak_kb'], native.THRESHOLD_KB + 100)
        self.assertEqual(set(p.name for p in self.root.iterdir()), {'active', 'profiles.json'})
        self.table.clear()
        event, = self.observer.tick(1004)
        self.assertEqual(event['measurement_complete'], 0)
        self.assertEqual(event['count'], 1)
        self.assertFalse(self.path.exists())

    def test_reused_root_cannot_claim_new_descendants(self):
        self.samples[10]['identity'] = 200
        event, = self.observer.tick(1002)
        self.assertNotIn('peak_kb', event)
        self.assertFalse((self.root / 'profiles.json').exists())

    def test_foreign_uid_and_reused_descendant_do_not_train(self):
        self.table[11]['uid'] = os.getuid() + 1
        self.observer.tick(1002)
        self.assertFalse((self.root / 'profiles.json').exists())
        self.table[11]['uid'] = os.getuid()
        self.samples[11]['footprint_kb'] = 100
        self.observer.tick(1004)
        self.samples[11] = dict(identity=220, footprint_kb=native.THRESHOLD_KB)
        self.observer.tick(1006)
        self.assertFalse((self.root / 'profiles.json').exists())

    def test_unchanged_escaped_child_is_observed_after_root_exits(self):
        self.observer.tick(1002)
        del self.table[10]
        self.table[11]['ppid'] = 1
        self.assertEqual(self.observer.tick(1004), [])
        self.assertTrue(self.path.exists())
        self.table.clear()
        event, = self.observer.tick(1006)
        self.assertEqual(event['count'], 2)

    def test_missing_samples_are_unknown_and_low_samples_never_train(self):
        self.samples.clear()
        self.assertEqual(self.observer.tick(1002), [])
        self.assertTrue(self.path.exists(), 'a failed footprint probe must retain observation')
        self.table.clear()
        event, = self.observer.tick(1004)
        self.assertEqual(event['count'], 0)
        self.assertNotIn('peak_kb', event)
        self.assertFalse((self.root / 'profiles.json').exists())

    def test_pid_reparenting_between_table_and_probe_discards_discovery(self):
        original = {p: dict(row) for p, row in self.table.items()}
        changed = {p: dict(row) for p, row in self.table.items()}
        changed[11]['ppid'] = 999
        self.observer.table = unittest.mock.Mock(side_effect=[original, changed])
        self.assertEqual(self.observer.tick(1002), [])
        self.assertFalse((self.root / 'profiles.json').exists())

    def test_process_table_probes_are_shared_across_native_jobs(self):
        for index in range(12):
            native.write_json(self.root / 'active' / f'{index}.json',
                              dict(pid=10, identity=100, fingerprint=self.fingerprint, created=1000))
        table = unittest.mock.Mock(return_value=self.table)
        self.observer.table = table
        self.observer.tick(1002)
        self.assertLessEqual(table.call_count, 2, 'native observation must not spawn ps per command')

    def test_invalid_profile_cannot_crash_collector(self):
        native.write_json(self.root / 'profiles.json', {'c' * 64: 42, self.fingerprint: {'peak_kb': 'bad'}})
        self.observer.tick(1002)
        self.assertEqual(native.read_json(self.root / 'profiles.json')[self.fingerprint]['peak_kb'], native.THRESHOLD_KB + 100)

    def test_public_or_symlink_observation_is_not_consumed(self):
        self.path.chmod(0o644)
        self.assertEqual(self.observer.tick(1002), [])
        self.assertFalse((self.root / 'profiles.json').exists())
        self.path.unlink()
        target = self.root / 'private.json'
        native.write_json(target, {'private': True})
        self.path.symlink_to(target)
        self.assertEqual(self.observer.tick(1004), [])
        self.assertEqual(native.read_json(target), {'private': True})

    def test_native_launch_execs_original_shell_once_without_worker_changes(self):
        from demand_policy import Decision
        with patch.object(sys, 'argv', ['_native', '--fingerprint', self.fingerprint,
                          '--shell', '/bin/zsh', '--login', '--shell-command', 'novel --read']), \
             patch('demand_policy.classify', return_value=Decision('light', 'unknown-demand', 'unknown', self.fingerprint, 0)), \
             patch.object(native, 'register') as register, patch.object(native.os, 'execvpe') as execute:
            native.main()
        register.assert_called_once_with(self.fingerprint, '')
        execute.assert_called_once_with('/bin/zsh', ['/bin/zsh', '-lc', 'novel --read'], os.environ)

    def test_changed_script_uses_admission_instead_of_stale_native_decision(self):
        from demand_policy import Decision
        class Exec(Exception):
            pass
        with patch.object(sys, 'argv', ['_native', '--fingerprint', self.fingerprint,
                          '--session-key', 'owner', '--shell-command', 'bash helper.sh']), \
             patch('demand_policy.classify', return_value=Decision('heavy', 'known-workload', 'evidence', 'c' * 64, 1)), \
             patch.object(native, 'register') as register, patch.object(native.os, 'execvpe', side_effect=Exec) as execute:
            with self.assertRaises(Exec):
                native.main()
        register.assert_not_called()
        argv = execute.call_args.args[1]
        self.assertIn('run', argv)
        self.assertIn('86400', argv)
        self.assertEqual(argv[-1], 'bash helper.sh')


if __name__ == '__main__':
    unittest.main()
