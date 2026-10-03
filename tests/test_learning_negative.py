"""Prove the new guards reject deliberate regressions; isolated unit fixtures."""
import io
import unittest
from unittest.mock import patch
from test_compiler_profiles import CompilerProfileTests
from test_job_observation import JobObservationTests
from test_sampling_context import SamplingContextTests
from test_learning_analytics import LearningAnalyticsTests


def rejects(case, name, target, replacement):
    with patch(target, replacement):
        result=unittest.TextTestRunner(stream=io.StringIO()).run(unittest.TestSuite([case(name)]))
    if result.wasSuccessful():
        raise AssertionError('negative control failed to detect '+name)


def main():
    rejects(CompilerProfileTests,'test_three_complete_builds_reuse_across_bounded_source_edits',
            'compiler_profiles.predict',lambda history,profile,prior,now:(prior,2))
    rejects(CompilerProfileTests,'test_incomplete_and_insufficient_evidence_never_lower',
            'compiler_profiles.predict',lambda history,profile,prior,now:(524288,1))
    rejects(JobObservationTests,'test_pid_reuse_and_missing_live_usage_fail_closed',
            'job_observation.sample_job',lambda *args,**kwargs:{'complete':True})
    rejects(SamplingContextTests,'test_session_metadata_shares_but_config_and_probe_controls_do_not',
            'scheduler_metrics.measurement_signature',lambda *args:'constant-unsafe-key')
    import admission
    original=admission.decide
    def stale_allowed(policy,sample,controller,jobs,job):
        return original(policy,{**sample,'monotonic':controller['now'],'busy':False},controller,jobs,job)
    rejects(SamplingContextTests,'test_stale_and_busy_are_distinguishable_and_never_allow',
            'admission.decide',stale_allowed)
    def failed_probe_escapes(job, table_reader, usage_reader, **kwargs):
        return table_reader()
    rejects(JobObservationTests,'test_process_probe_errors_are_incomplete_observations_not_runner_failures',
            'job_observation.sample_job',failed_probe_escapes)
    rejects(JobObservationTests,'test_terminal_gap_cannot_certify_an_unobserved_peak',
            'job_observation.complete_at',lambda state,now:state.get('complete',False))
    rejects(CompilerProfileTests,'test_subdirectory_cannot_certify_unenumerated_parent_module_inputs',
            'compiler_profiles.compiler_profile',lambda *args,**kwargs:{'key':'unchecked-subdirectory'})
    print('8/8 deliberate learning and sampling regressions detected')


if __name__=='__main__': main()
