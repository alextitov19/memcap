"""Learning success means useful admission, not merely a larger event count."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'libexec'))
from analytics_events import make_event
from analytics_reports import summarize, compare


class LearningAnalyticsTests(unittest.TestCase):
    def row(self,event,seq,**fields):
        return make_event(event,fields,b'x'*32,'a'*32,seq,build='b'*64,policy='c'*64,boot='d'*32,wall=1000+seq,mono=seq)

    def test_unknown_history_is_not_zero_benefit(self):
        report=summarize([self.row('admitted',1,job='old',request_kb=1048576)])
        self.assertEqual(report['learning_effectiveness']['known_admissions'],0)
        self.assertIsNone(report['learning_effectiveness']['admissions_below_prior'])
        self.assertIsNone(report['learning_effectiveness']['sampling_busy_count'])

    def test_actual_lower_admission_separate_from_complete_observation(self):
        rows=[self.row('admitted',1,job='one',request_kb=524288,estimate_prior_kb=1048576,compiler_profile_used=1,estimate_reuse_reason=1),
              self.row('completed',2,job='one',runtime_ms=2800,learning_complete=1,learning_protocol=2,observation_probe_ms=12,sampling_busy_count=3,sampling_expired_count=1),
              self.row('admitted',3,job='two',request_kb=1048576,estimate_prior_kb=1048576,compiler_profile_used=0,estimate_reuse_reason=2),
              self.row('completed',4,job='two',runtime_ms=10000,learning_complete=1,learning_protocol=2,observation_probe_ms=20,sampling_busy_count=0,sampling_expired_count=0),
              self.row('completed',5,job='legacy',runtime_ms=10000,learning_complete=1,learning_protocol=1,observation_probe_ms=0)]
        result=summarize(rows)['learning_effectiveness']
        self.assertEqual(result['known_admissions'],2)
        self.assertEqual(result['admissions_below_prior'],1)
        self.assertEqual(result['compiler_profile_admissions'],1)
        self.assertEqual(result['sampling_busy_count'],3)
        self.assertEqual(result['sampling_expired_count'],1)
        self.assertEqual(result['owned_observation_completions'],2)
        self.assertEqual(result['owned_probe_ms']['n'],2)
        self.assertEqual(result['owned_probe_ms']['median'],16)

    def completed_work(self,workload,context=None,workers=1):
        fields=dict(job='job',workload=workload,workers=workers,queue_wait_ms=10,cache_state='warm')
        if context:
            fields['compiler_context']=context
        return [self.row('admitted',1,**fields),
                self.row('completed',2,job='job',runtime_ms=100,exit_code=0)]

    def test_new_context_metadata_preserves_exact_comparisons_with_old_releases(self):
        baseline=self.completed_work('same-workload')
        candidate=self.completed_work('same-workload','new-context')
        result=compare(baseline,candidate)
        self.assertEqual(len(result['cohorts']),1)
        self.assertEqual(result['cohorts'][0]['workload_match'],'exact')

    def test_context_comparisons_remain_exploratory_and_worker_scoped(self):
        baseline=self.completed_work('before-edit','same-context')
        candidate=self.completed_work('after-edit','same-context')
        result=compare(baseline,candidate)
        self.assertEqual(len(result['cohorts']),1)
        self.assertEqual(result['cohorts'][0]['workload_match'],'compiler_context')
        self.assertEqual(result['cohorts'][0]['evidence'],'exploratory')
        self.assertEqual(compare(baseline,self.completed_work('after-edit','same-context',2))['cohorts'],[])
        both=compare(baseline,baseline)
        self.assertEqual({r['workload_match'] for r in both['cohorts']},{'exact','compiler_context'})


if __name__=='__main__':unittest.main()
