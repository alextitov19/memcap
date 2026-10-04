"""Prove the new guards reject deliberate regressions; isolated unit fixtures."""
import io
import unittest
import sys
from unittest.mock import patch
from test_compiler_profiles import CompilerProfileTests
from test_job_observation import JobObservationTests
from test_sampling_context import SamplingContextTests
from test_learning_analytics import LearningAnalyticsTests
from test_learning_integration import LearningIntegrationTests
from test_compiler_commands import CompilerCommandTests
from test_job_timing import JobTimingTests


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
    from analytics_reports import compare
    def dropped_exact_matches(*args):
        result=compare(*args)
        return {**result,'cohorts':[r for r in result['cohorts'] if r['workload_match']!='exact']}
    rejects(LearningAnalyticsTests,'test_new_context_metadata_preserves_exact_comparisons_with_old_releases',
            'test_learning_analytics.compare',dropped_exact_matches)
    from compiler_profiles import predict
    def restored_default_floor(history,profile,prior,now):
        return predict(history,profile,max(prior,1048576),now)
    rejects(LearningIntegrationTests,'test_unavailable_compiler_reuse_preserves_exact_learning_and_growth',
            'compiler_profiles.predict',restored_default_floor)
    from scheduler import Scheduler
    observe=Scheduler.observe_owned
    def exit_poisoned(self,data,job,sample):
        if not job.get('footprint_members',job['members']):
            sample={**sample,'complete':False,'missing':sample.get('missing',0)+1}
        return observe(self,data,job,sample)
    rejects(LearningIntegrationTests,'test_complete_sample_survives_exit_before_registry_refresh',
            'scheduler.Scheduler.observe_owned',exit_poisoned)
    rejects(CompilerCommandTests,'test_literal_directory_environment_and_go_directory_flag_reach_profile',
            'compiler_commands.resolve',lambda *args:None)
    rejects(SamplingContextTests,'test_busy_sampler_rereads_newly_published_fresh_sample',
            'scheduler_metrics.shared_sample',lambda *args:{'busy':True})
    rejects(JobObservationTests,'test_failure_diagnostics_distinguish_missing_usage_identity_and_scope',
            'job_observation.sample_job',lambda *args,**kwargs:{'reasons':{}})
    from compiler_commands import resolve
    def ignore_cdpath(argv,cwd,env,*args):
        return resolve(argv,cwd,{k:v for k,v in env.items() if k!='CDPATH'},*args)
    rejects(CompilerCommandTests,'test_cdpath_cannot_redirect_profile_to_different_compiler_inputs',
            'compiler_commands.resolve',ignore_cdpath)
    rejects(JobObservationTests,'test_terminal_empty_requires_exit_empty_group_and_no_probe_fault',
            'job_observation.terminal_empty',lambda *args:True)
    import job_observation
    sample_job = job_observation.sample_job
    def discard_unpaired(*args, **kwargs):
        sample = sample_job(*args, **kwargs)
        sample['peak_kb'] = sum(v for p, v in sample['footprints'].items()
                                if p in sample['usage_identities'])
        return sample
    rejects(JobObservationTests, 'test_verified_growth_survives_exit_before_second_read_without_certifying_complete',
            'job_observation.sample_job', discard_unpaired)
    import compiler_profiles
    profile = compiler_profiles.compiler_profile
    def waste_source_reads(argv, cwd, *args, **kwargs):
        from pathlib import Path
        for source in Path(cwd).glob('*.go'):
            source.read_bytes()
        return profile(argv, cwd, *args, **kwargs)
    rejects(CompilerProfileTests, 'test_large_source_envelope_uses_metadata_budget_without_reading_source_contents',
            'compiler_profiles.compiler_profile', waste_source_reads)
    import job_timing
    fields = job_timing.phase_fields
    def awake_as_elapsed(job, phase):
        result = fields(job, phase)
        result[phase + '_elapsed_ms'] = result.get(phase + '_awake_ms')
        return result
    rejects(JobTimingTests, 'test_sleep_and_wall_adjustments', 'job_timing.phase_fields', awake_as_elapsed)
    count=19
    if sys.platform=='darwin':
        rejects(LearningIntegrationTests,'test_exit_between_first_poll_and_empty_probe_preserves_prior_evidence',
                'job_observation.terminal_empty',lambda *args:False)
        count+=1
    print(f'{count}/{count} deliberate learning and sampling regressions detected')


if __name__=='__main__': main()
