"""Deliberately disable fixes and require their regression tests to fail."""
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import test_throughput as checks
import test_environments as environments
import test_release_evidence as releases


def main():
    original = checks.summarize
    def hide_pending(*args, **kwargs):
        result = original(*args, **kwargs)
        result['pending']['age_ms']['max'] = 0
        return result
    launch = releases.Scheduler.launch
    def forget_fixed_learning(self, argv, cwd, job, data):
        result = launch(self, argv, cwd, job, data)
        job['estimate_key'] = ''
        return result
    controls = [
        (checks.ThroughputTests, 'test_pending_all_night_is_visible_and_not_a_started_job',
         patch.object(checks, 'summarize', hide_pending)),
        (checks.ThroughputTests, 'test_worker_change_only_lowers_with_complete_exact_evidence',
         patch.object(checks, 'pending_request', lambda job, *_: job['memory_kb'])),
        (checks.ThroughputTests, 'test_explicit_wrappers_classify_cleanup_and_remote_reads_natively',
         patch.object(checks, 'native_command', lambda *_: False)),
        (environments.EnvironmentTests, 'test_foreign_label_auto_remove_and_recreated_container_are_not_owned',
         patch.object(environments.e, 'identities', lambda rows, _: {r['Id']: r['Created'] for r in rows})),
        (releases.ReleaseEvidenceTests, 'test_sampling_notice_preserves_last_valid_headroom',
         patch.object(releases, 'capacity_message', lambda _: 'observed 0.00 GiB')),
        (releases.ReleaseEvidenceTests, 'test_fixed_launch_trains_actual_workers_without_changing_floor',
         patch.object(releases.Scheduler, 'launch', forget_fixed_learning)),
    ]
    for cls, name, mutation in controls:
        output = io.StringIO()
        with mutation:
            result = unittest.TextTestRunner(stream=output).run(unittest.TestSuite([cls(name)]))
        if result.wasSuccessful() or result.errors or len(result.failures) != 1:
            print(output.getvalue())
            raise AssertionError('negative control did not fail at its assertion: ' + name)
        print('Negative control rejected: ' + name)
    print('6/6 deliberate regressions detected')


if __name__ == '__main__':
    main()
