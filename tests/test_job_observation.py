"""Identity and churn fixtures never inspect or signal real processes."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'libexec'))


class JobObservationTests(unittest.TestCase):
    def table(self):
        return {'10':dict(ppid=1,group=10,uid=501,start='leader'),
                '11':dict(ppid=10,group=10,uid=501,start='child'),
                '12':dict(ppid=10,group=12,uid=501,start='escaped'),
                '13':dict(ppid=10,group=10,uid=502,start='foreign')}

    def job(self):
        return dict(group=10, owner=99, members={'10':'leader','11':'child'},
                    footprint_members={'10':'leader','11':'child'}, status='running')

    def test_complete_owned_group_includes_escaped_descendants_not_foreign_uid(self):
        from job_observation import sample_job
        result=sample_job(self.job(), lambda:self.table(), lambda pid:dict(identity=int(pid),footprint_kb=100),uid=501)
        self.assertTrue(result['complete'])
        self.assertEqual(result['peak_kb'],300)
        self.assertEqual(set(result['identities']),{'10','11','12'})

    def test_pid_reuse_and_missing_live_usage_fail_closed(self):
        from job_observation import sample_job
        calls={}
        def reused(pid):
            calls[pid]=calls.get(pid,0)+1
            return dict(identity=calls[pid],footprint_kb=100)
        self.assertFalse(sample_job(self.job(),lambda:self.table(),reused,uid=501)['complete'])
        self.assertFalse(sample_job(self.job(),lambda:self.table(),lambda pid:None,uid=501)['complete'])
        previous={**self.job(),'owned_observation':{'usage_identities':{'10':999}}}
        self.assertFalse(sample_job(previous,lambda:self.table(),lambda pid:dict(identity=int(pid),footprint_kb=100),uid=501)['complete'])

    def test_failure_diagnostics_distinguish_missing_usage_identity_and_scope(self):
        from job_observation import sample_job, accumulate
        missing=sample_job(self.job(),lambda:self.table(),lambda pid:None,uid=501)
        self.assertEqual(missing['reasons']['usage'],3)
        empty=sample_job(self.job(),lambda:{},lambda pid:None,uid=501)
        self.assertEqual(empty['reasons']['anchor'],1)
        previous={**self.job(),'owned_observation':{'usage_identities':{'10':999}}}
        reused=sample_job(previous,lambda:self.table(),lambda pid:dict(identity=int(pid),footprint_kb=100),uid=501)
        self.assertEqual(reused['reasons']['identity'],1)
        state=accumulate({},missing)
        self.assertEqual(state['reasons']['usage'],3)
        self.assertFalse(state['complete'])

    def test_new_member_after_measurement_is_incomplete(self):
        from job_observation import sample_job
        before=self.table();after={**before,'14':dict(ppid=10,group=10,uid=501,start='new')}
        tables=iter([before,after])
        self.assertFalse(sample_job(self.job(),lambda:next(tables),lambda pid:dict(identity=int(pid),footprint_kb=100),uid=501)['complete'])

    def test_process_probe_errors_are_incomplete_observations_not_runner_failures(self):
        from job_observation import sample_job
        from scheduler import QueueError
        from subprocess import TimeoutExpired
        for error in (QueueError('temporary process identity failure'), TimeoutExpired('ps',5)):
            def unavailable():
                raise error
            with self.subTest(error=type(error).__name__):
                result=sample_job(self.job(),unavailable,lambda pid:None,uid=501)
                self.assertFalse(result['complete'])
                self.assertEqual(result['fault'],1)

    def test_nested_registered_group_is_not_charged_to_parent(self):
        from job_observation import sample_job
        job={**self.job(),'observation_exclusions':{'12':'escaped'}}
        result=sample_job(job,lambda:self.table(),lambda pid:dict(identity=int(pid),footprint_kb=100),uid=501)
        self.assertTrue(result['complete'])
        self.assertEqual(result['peak_kb'],200)

    def test_vanished_or_reused_recorded_leader_cannot_certify_zero(self):
        from job_observation import sample_job
        for rows in ({}, {'10':dict(ppid=1,group=10,uid=501,start='replacement')}):
            result=sample_job(self.job(),lambda:rows,lambda pid:dict(identity=int(pid),footprint_kb=100),uid=501)
            self.assertFalse(result['complete'])

    def test_accumulator_short_observations_complete_but_gaps_and_faults_do_not(self):
        from job_observation import accumulate
        sample=dict(complete=True,peak_kb=100,identities={'10':'leader'},at=1)
        state=accumulate({},sample)
        state=accumulate(state,{**sample,'at':1.25,'peak_kb':200})
        self.assertTrue(state['complete'])
        self.assertEqual(state['samples'],2)
        self.assertEqual(state['peak_kb'],200)
        self.assertFalse(accumulate(state,{**sample,'at':10})['complete'])
        self.assertFalse(accumulate(state,{**sample,'at':1.5,'complete':False})['complete'])
        self.assertFalse(accumulate({}, {**sample,'complete':False,'peak_kb':0})['complete'])
        self.assertFalse(accumulate({'began':0},{**sample,'at':10})['complete'])

    def test_terminal_gap_cannot_certify_an_unobserved_peak(self):
        from job_observation import complete_at
        state=dict(complete=True,last=100)
        self.assertTrue(complete_at(state,101))
        self.assertFalse(complete_at(state,106))
        self.assertFalse(complete_at(state,99))
        self.assertFalse(complete_at({'complete':True},101))

    def test_terminal_empty_requires_exit_empty_group_and_no_probe_fault(self):
        from job_observation import terminal_empty
        empty=dict(identities={},fault=0)
        self.assertTrue(terminal_empty(empty,0,{}))
        self.assertFalse(terminal_empty(empty,None,{}))
        self.assertFalse(terminal_empty(empty,0,{'10':'live'}))
        self.assertFalse(terminal_empty({**empty,'fault':1},0,{}))
        self.assertFalse(terminal_empty({**empty,'identities':{'10':'measured'}},0,{}))


if __name__=='__main__': unittest.main()
