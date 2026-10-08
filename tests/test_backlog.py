"""Release regressions use temporary state and synthetic clocks/process trees."""
import json
import os
from pathlib import Path
import sys
import time
import tempfile
import gzip
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'libexec'))


class BacklogTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        environment=patch.dict(os.environ,HOME=tmp.name,MEMCAP_STATE_HOME=tmp.name+'/state',
                               MEMCAP_CONFIG_HOME=tmp.name+'/config',MC_DRY_RUN='1',MC_DOCKER_RUNTIME='none')
        environment.start()
        self.addCleanup(environment.stop)

    def row(self, event, seq):
        from analytics_events import make_event
        return make_event(event, {'demand': 'light', 'route': 'native'}, b'x'*32,
                          'a'*32, seq, build='b'*64, policy='c'*64, boot='d'*32,
                          wall=1000+seq, mono=seq)

    def test_storage_pressure_preserves_decisions_before_routine_hook_volume(self):
        from analytics_store import Store, read_rows
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'analytics'
            with Store(root) as store:
                for seq in range(1,211):
                    store.insert(self.row('classification' if seq<=10 else 'hook',seq))
                store.db.commit()
                pages=store.db.execute('PRAGMA page_count').fetchone()[0]
                store.db.execute(f'PRAGMA max_page_count={pages}')
                store.maintain(now=2000)
                remaining=store.rows()
                self.assertEqual(sum(r['event']=='classification' for r in remaining),10)
                self.assertLess(len(remaining),210)
            rows,health=read_rows(root)
            self.assertEqual(health['coverage_by_event']['classification']['oldest_wall'],1001)
            self.assertGreater(health['evicted_events'],0)

    def test_sampler_handoff_covers_measured_probe_latency_without_extra_probe(self):
        from scheduler_metrics import shared_sample
        now=[100.0]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            def advance(seconds):
                now[0]+=seconds
                if now[0]>=100.5:
                    (root/'sample.json').write_text(json.dumps({'key':'same','sample':{'pressure':2,'monotonic':100.5}}))
            with patch('scheduler_metrics.fcntl.flock',side_effect=BlockingIOError), \
                 patch('scheduler_metrics.time.monotonic',side_effect=lambda:now[0]), \
                 patch('scheduler_metrics.time.sleep',side_effect=advance):
                result=shared_sample(root,'same',lambda:self.fail('duplicate host probe'))
            self.assertFalse(result.get('busy',False))
            self.assertEqual(result['sampling_path'],3)
            self.assertLessEqual(now[0]-100,1.05)

    def test_direct_runner_classification_has_positive_evidence_and_original_argv(self):
        from command_stages import command_decision
        with tempfile.TemporaryDirectory() as tmp:
            read=['/bin/bash','-c','rg needle .']
            before=list(read)
            self.assertEqual(command_decision(read,tmp).kind,'light')
            self.assertEqual(read,before)
            self.assertEqual(command_decision(['/bin/bash','-c','go test ./...'],tmp).kind,'heavy')

    def test_cli_records_routing_evidence_and_executes_native_command_once(self):
        import scheduler
        events=[]
        class Executed(Exception): pass
        words=['/bin/bash','-c','printf "%s" "literal $value"']
        def execute(executable,argv,environ):
            self.assertEqual(argv,words)
            raise Executed()
        with patch.object(sys,'argv',['memcap','run','--',*words]), \
             patch('analytics_events.emit',side_effect=lambda event,**fields:events.append((event,fields))), \
             patch('scheduler.os.execvpe',side_effect=execute), \
             patch.object(scheduler.Scheduler,'run',side_effect=AssertionError('light command queued')), \
             patch('scheduler.os.chdir'):
            with self.assertRaises(Executed): scheduler.main()
        self.assertEqual([e for e,_ in events],['classification','route'])
        self.assertEqual(events[0][1]['demand'],'light')
        self.assertEqual(events[1][1]['route'],'native')

    def test_cli_heavy_route_retains_demand_reason_for_job_diagnostics(self):
        import scheduler
        seen=[]
        def run(instance,*args):
            seen.append(instance.analytics_metadata)
            self.assertGreater(instance.classification_code,0)
            return 7
        with patch.object(sys,'argv',['memcap','run','--','go','test','./...']), \
             patch.object(scheduler.Scheduler,'run',new=run),patch('analytics_events.emit'):
            self.assertEqual(scheduler.main(),7)
        self.assertEqual(seen[0]['demand'],'heavy')
        self.assertEqual(seen[0]['demand_reason'],'known-workload')

    def test_incident_bundle_survives_raw_eviction_and_never_contains_commands(self):
        from analytics_store import Store
        from incident_evidence import capture
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'memcap'
            with Store(root/'analytics') as store:
                row=self.row('stalled',1)
                row['command']='private credential'
                store.insert(row)
            path=capture(root,now=1100)
            self.assertIsNotNone(path)
            bundle=json.loads(gzip.decompress(Path(path).read_bytes()))
            self.assertEqual(len(bundle['events']),1)
            self.assertNotIn('private credential',json.dumps(bundle))
            with Store(root/'analytics') as store:
                store.evict(100)
            self.assertEqual(len(json.loads(gzip.decompress(Path(path).read_bytes()))['events']),1)
            self.assertEqual(Path(path).stat().st_mode & 0o777,0o600)

    def test_trace_expiry_is_visible_without_mutating_or_renewing_capture(self):
        from command_trace import status
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)
            path.chmod(0o700)
            (path/'enabled.json').write_text('{"until":100}')
            (path/'enabled.json').chmod(0o600)
            original=(path/'enabled.json').read_bytes()
            self.assertEqual(status(path,now=101)['state'],'expired')
            self.assertFalse(status(path,now=101)['enabled'])
            self.assertEqual((path/'enabled.json').read_bytes(),original)

    def test_incident_bounds_expiry_and_unavailable_storage(self):
        from analytics_store import Store
        from incident_evidence import capture, maintain
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with Store(root/'analytics') as store:
                store.insert(self.row('stalled',1))
            for _ in range(22):
                self.assertIsNotNone(capture(root,now=1100))
            paths=list((root/'incidents').glob('incident-*.json.gz'))
            self.assertEqual(len(paths),20)
            self.assertTrue(all(p.stat().st_size<=1024*1024 for p in paths))
            for path in paths:
                os.utime(path,(1,1))
            maintain(root,now=8*86400)
            self.assertEqual(list((root/'incidents').glob('incident-*.json.gz')),[])
            with patch('analytics_store.read_rows',side_effect=OSError('unavailable')):
                self.assertIsNone(capture(root,now=1100))

    def test_expired_trace_is_pruned_without_another_owner_command(self):
        from command_trace import maintain, status
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)
            path.chmod(0o700)
            (path/'enabled.json').write_text('{"until":100}')
            (path/'enabled.json').chmod(0o600)
            record=path/'commands.jsonl'
            record.write_text('private fixture')
            record.chmod(0o600)
            os.utime(record,(1,1))
            maintain(path,now=90000)
            self.assertFalse(record.exists())
            self.assertEqual(status(path,now=90000)['state'],'expired')

    def test_runtime_diagnostics_distinguish_owner_trust_from_configuration(self):
        from integrate import runtime_issues
        rows=[dict(eventName='preToolUse',enabled=True,trustStatus='modified')]
        problems=runtime_issues(rows,configuration_matches=True)
        self.assertTrue(any('modified' in p and '/hooks' in p for p in problems))
        self.assertFalse(any('configuration' in p for p in problems))
        self.assertEqual(runtime_issues([{**rows[0],'trustStatus':'trusted'}],True),[])
        self.assertTrue(any('disabled' in p for p in runtime_issues([{**rows[0],'enabled':False}],True)))

    def test_child_birth_during_probe_can_recover_only_after_that_child_is_measured(self):
        from job_observation import sample_job, reconcile, complete_at
        leader={'10':dict(ppid=1,group=10,uid=501,start='leader')}
        child={**leader,'11':dict(ppid=10,group=10,uid=501,start='child')}
        job=dict(group=10,owner=99,members={'10':'leader'})
        read=lambda pid:dict(identity=pid,footprint_kb=100)
        initial=sample_job(job,lambda:leader,read,uid=501)
        initial['at']=100
        state,_=reconcile({'began':99.9},initial,{'10':'leader'})
        tables=iter([leader,child])
        birth=sample_job(job,lambda:next(tables),read,uid=501)
        birth['at']=100.2
        self.assertFalse(birth['complete'])
        pending,_=reconcile(state,birth,{'10':'leader','11':'child'})
        self.assertFalse(complete_at(pending,100.3))
        measured=sample_job(job,lambda:child,read,uid=501)
        measured['at']=100.4
        recovered,_=reconcile(pending,measured,{'10':'leader','11':'child'})
        self.assertTrue(complete_at(recovered,100.5))
        # A child disappearing before the registry refresh is still missing
        # evidence. Its birth cannot vanish just because current membership did.
        lost,_=reconcile(state,birth,{'10':'leader'})
        initial['at']=100.4
        lost,_=reconcile(lost,initial,{'10':'leader'})
        self.assertFalse(complete_at(lost,100.5))
        # A recycled PID born during this probe must not replace an older
        # unmeasured pending identity without recording the lost obligation.
        old_pending={**state,'pending':{'11':'previous-child'}}
        recycled,_=reconcile(old_pending,birth,{'10':'leader','11':'child'})
        recycled,_=reconcile(recycled,measured,{'10':'leader','11':'child'})
        self.assertFalse(complete_at(recycled,100.5))

    def test_adaptive_large_request_uses_physical_capacity_not_planning_target(self):
        from scheduler import Scheduler, QueueError
        gib=1048576
        def sample():
            return dict(cap_kb=gib,tracked_kb=0,available_kb=4*gib,pressure=2,
                        fault=False,footprints={},tracked_pids=[],boot_id='fixture',monotonic=time.monotonic())
        with tempfile.TemporaryDirectory() as tmp, \
             patch('scheduler.current_pressure',return_value=2), \
             patch('orphan_recovery.agent_identity',return_value={}):
            q=Scheduler(Path(tmp)/'adaptive',sampler=sample,policy='adaptive',
                        max_pressure='yellow',memory_gb=2,headroom_gb=.5,poll=.01)
            with q.locked() as data:
                data['controller']=dict(boot_id='fixture',healthy_since=0,last_start=0)
                q.save(data)
            self.assertEqual(q.run([sys.executable,'-c','pass'],memory_gb=2,wait=5),0)
            strict=Scheduler(Path(tmp)/'strict',sampler=sample,max_pressure='yellow',memory_gb=2,poll=.01)
            with self.assertRaisesRegex(QueueError,'exceeds'):
                strict.run([sys.executable,'-c','pass'],memory_gb=2,wait=5)
            blocked=Scheduler(Path(tmp)/'blocked',sampler=lambda:{**sample(),'available_kb':gib},
                              policy='adaptive',max_pressure='yellow',memory_gb=2,poll=.01)
            with blocked.locked() as data:
                data['controller']=dict(boot_id='fixture',healthy_since=0,last_start=0)
                blocked.save(data)
            marker=Path(tmp)/'must-not-start'
            self.assertEqual(blocked.run([sys.executable,'-c',f'open({str(marker)!r},"w").close()'],
                                         memory_gb=2,wait=.03),75)
            self.assertFalse(marker.exists())
            self.assertEqual(blocked.last_decision['reason'],'headroom')

    def test_runner_reclassification_does_not_double_count_hook_decisions(self):
        from analytics_reports import summarize
        hook={**self.row('classification',1),'source':'hook','demand':'heavy'}
        runner={**self.row('classification',2),'source':'scheduler','demand':'heavy'}
        done={**self.row('completed',3),'job':'e'*32,'exit_code':0,'learning_complete':0}
        report=summarize([hook,runner,done])
        self.assertEqual(report['classification']['decisions'],{'heavy':1})
        self.assertEqual(report['classification']['runner_decisions'],{'heavy':1})
        self.assertEqual(report['completed_evidence']['successful_learning_incomplete'],1)

    def test_python_filename_is_not_heavy_evidence_but_actual_test_execution_is(self):
        from demand_policy import classify
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            read=root/'test_ssm_parameters.py'
            read.write_text('import json\nprint(json.dumps({"ok": True}))\n')
            self.assertEqual(classify('python3 test_ssm_parameters.py',root).kind,'light')
            read.write_text('import unittest\nunittest.main()\n')
            self.assertEqual(classify('python3 test_ssm_parameters.py',root).kind,'heavy')
            read.write_text('import subprocess\nsubprocess.run(["go","test","./..."])\n')
            self.assertEqual(classify('python3 test_ssm_parameters.py',root).kind,'heavy')
            read.write_text('import unittest\ndef unused():\n    unittest.main()\nprint("read")\n')
            self.assertEqual(classify('python3 test_ssm_parameters.py',root).kind,'light')


if __name__=='__main__': unittest.main()
