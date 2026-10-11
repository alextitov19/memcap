"""Release incident contracts: synthetic commands, private temporary evidence."""
import gzip
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'libexec'))
from analytics_events import make_event
from analytics_reports import summarize
from analytics_store import Store, read_rows
from demand_policy import classify
from scheduler_policy import hook_response


class Incidents(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        env = patch.dict(os.environ, HOME=self.tmp.name, MC_DRY_RUN='1',
                         MEMCAP_STATE_HOME=str(self.root/'state'),
                         MEMCAP_CONFIG_HOME=str(self.root/'config'))
        env.start()
        self.addCleanup(env.stop)

    def row(self, event, seq, **fields):
        return make_event(event, fields, b'x'*32, 'a'*32, seq,
                          build='b'*64, policy='c'*64, boot='d'*32,
                          wall=1000+seq, mono=seq)

    def test_explicit_wait_and_read_stay_native(self):
        from agent_diagnostics import guidance
        command = 'memcap wait --session --timeout 60; cat /tmp/task.output'
        self.assertEqual(classify(command, self.root).kind, 'light')
        payload = dict(hook_event_name='PostToolUse', tool_name='Bash',
                       tool_input={'command':command}, tool_response={'exit_code':0, 'output':
                           'memcap: --session no longer pending. Read the ORIGINAL task output.\nexit 0'})
        text = guidance(payload, self.root)
        self.assertIn('explicit wait', text)
        self.assertIn('separate tool call', text)
        self.assertNotIn('MUST report', text)

    def test_android_listing_is_native_but_boot_remains_managed(self):
        for text in ['emulator -list-avds', '~/Library/Android/sdk/emulator/emulator -list-avds',
                     'ls /sdk/emulator && /sdk/emulator -list-avds; cat owner.sh | head -20']:
            self.assertEqual(classify(text,self.root).kind,'light',text)
        for text in ['emulator -avd Fixture_Phone', 'emulator -list-avds -avd Fixture_Phone',
                     'emulator -list-avds; emulator -avd Fixture_Phone',
                     'emulator -list-avds $(go test ./...)']:
            self.assertEqual(classify(text,self.root).kind,'heavy',text)

    def test_compound_browser_work_explains_positive_evidence(self):
        (self.root/'walk.mjs').write_text("import { chromium } from '@playwright/test';\n")
        command = 'python3 -c "print(1)"; node walk.mjs'
        payload = dict(hook_event_name='PreToolUse', tool_name='Bash',
                       session_id='fixture', agent_id='child', cwd=str(self.root),
                       tool_input={'command':command})
        result = hook_response(payload, '/fixture/memcap', 'claude')
        self.assertIn('browser-script', result['hookSpecificOutput']['additionalContext'])
        self.assertIn('separate tool calls', result['hookSpecificOutput']['additionalContext'])
        payload['tool_input']['command'] = 'cat walk.mjs'
        self.assertEqual(hook_response(payload, '/fixture/memcap', 'claude'), {})

    def test_cancellation_request_does_not_claim_workload_completion(self):
        from scheduler_metrics import cancellation_fields
        fields = cancellation_fields(15, 1)
        self.assertEqual(fields, {'cancellation_signal':15, 'cancellation_stage':1})
        rows = [self.row('admitted',1,job='fixture'),
                self.row('cancellation',2,job='fixture',**fields)]
        report = summarize(rows)
        self.assertEqual(report['jobs']['unfinished'],1)
        self.assertEqual(report['cancellations']['requests'],1)
        self.assertNotIn('sender', report['cancellations'])

    def test_guarded_signal_uses_same_job_reference_as_supervisor(self):
        import analytics
        with patch('analytics.emit') as emit:
            self.assertEqual(analytics.main(['_signal','scheduled','15','1','2','action',
                                            '--job-ref','42']),0)
        self.assertEqual(emit.call_args.kwargs['job'],'42')
        self.assertNotIn('pid',emit.call_args.kwargs)

    def test_cancellation_and_action_survive_raw_eviction(self):
        root = self.root/'analytics'
        with Store(root) as store:
            store.insert(self.row('admitted',1,job='fixture'))
            store.insert(self.row('cancellation',2,job='fixture',cancellation_stage=1,cancellation_signal=15))
            store.insert(self.row('cancellation',3,job='fixture',cancellation_stage=3,cancellation_signal=15))
            store.insert(self.row('action',4,action='signal',scope='oversized',signal=15,signal_result=1))
            store.evict(100)
        rows, _ = read_rows(root)
        self.assertEqual(sum(r['event']=='cancellation' for r in rows),2)
        self.assertEqual(sum(r['event']=='action' for r in rows),1)

    def test_checkpoints_outlive_raw_retention_but_are_not_unbounded(self):
        root=self.root/'analytics'
        with Store(root) as store:
            store.insert(self.row('completed',1,job='fixture',exit_code=0))
            store.insert(self.row('action',2,action='signal',scope='oversized',signal=15))
            store.maintain(now=1002+30*86400)
            self.assertEqual(store.rows(),[])
            retained,_=read_rows(root)
            self.assertEqual({r['event'] for r in retained},{'completed','action'})
            store.maintain(now=1002+91*86400)
            self.assertEqual(read_rows(root)[0],[])

    def test_incident_capture_prioritizes_lifecycle_over_sample_volume(self):
        from incident_evidence import capture
        state = self.root/'memcap'
        with Store(state/'analytics') as store:
            store.insert(self.row('action',1,action='signal',scope='oversized',signal=15))
            for seq in range(2,2105):
                row = self.row('sample',seq,pressure=1)
                row['wall'] = 1002+seq/10
                store.insert(row)
        result = capture(state, now=1500)
        bundle = json.loads(gzip.decompress(Path(result).read_bytes()))
        self.assertTrue(bundle['truncated'])
        self.assertLessEqual(len(bundle['events']),2000)
        self.assertTrue(any(r['event']=='action' for r in bundle['events']))

    def test_learning_separates_short_unobserved_runs_and_probe_faults(self):
        rows = [self.row('completed',1,job='short',exit_code=0,runtime_ms=100,
                        learning_protocol=2,learning_complete=0,learning_samples=0),
                self.row('completed',2,job='fault',exit_code=0,runtime_ms=10000,
                         learning_protocol=2,learning_complete=0,learning_samples=2,observation_fault=1)]
        evidence = summarize(rows)['learning_effectiveness']['observation_outcomes']
        self.assertEqual(evidence['short_without_samples'],1)
        self.assertEqual(evidence['probe_fault'],1)
        self.assertEqual(evidence['complete'],0)

    def test_long_wait_includes_observed_blockers(self):
        rows = [self.row('admitted',1,job='slow',queue_wait_ms=90000),
                self.row('completed',2,job='slow',exit_code=0,runtime_ms=1000,blocked_slots_ms=88000)]
        worst = summarize(rows)['worst_waits'][0]
        self.assertEqual(worst['blocker_observed_ms'],{'slots':88000})

    def test_sampling_retry_uses_short_backoff_only_for_sample_unavailability(self):
        from throughput import admission_retry_delay
        self.assertEqual(admission_retry_delay(2, {'reason':'sampling'}),.5)
        self.assertEqual(admission_retry_delay(.1, {'reason':'sampling'}),.1)
        for reason in ['slots','headroom','measurement','pressure_or_measurement','startup','paging']:
            self.assertEqual(admission_retry_delay(2, {'reason':reason}),2)

    def test_release_compare_includes_matching_workload_evidence(self):
        from analytics_releases import snapshot, compare_snapshots
        before=[self.row('admitted',1,job='one',workload='same',workers=1,queue_wait_ms=100,cache_state='warm',paused=0),
                self.row('completed',2,job='one',exit_code=0,runtime_ms=1000,paused=0)]
        after=[self.row('admitted',3,job='two',workload='same',workers=1,queue_wait_ms=20,cache_state='warm',paused=0),
               self.row('completed',4,job='two',exit_code=0,runtime_ms=1000,paused=0)]
        snapshot(before,{},self.root/'before',since=0,until=2000)
        snapshot(after,{},self.root/'after',since=0,until=2000)
        result=compare_snapshots(self.root/'before',self.root/'after')
        self.assertEqual(result['matched_workloads']['cohorts'][0]['workload_match'],'exact')
        self.assertFalse(result['causal'])

    def test_report_operation_is_private_and_preserves_matching_decisions(self):
        from incident_evidence import capture
        state=self.root/'memcap'
        with Store(state/'analytics') as store:
            (state/'analytics/key').write_bytes(b'x'*32)
            (state/'analytics/key').chmod(0o600)
            store.insert(self.row('classification',1,operation='tool_fixture',demand='light'))
            for seq in range(2,2105):
                row=self.row('sample',seq,pressure=1);row['wall']=1002+seq/10
                store.insert(row)
        path=capture(state,now=1500,operation='tool_fixture')
        data=json.loads(gzip.decompress(Path(path).read_bytes()))
        self.assertEqual(data['selection']['matched_events'],1)
        self.assertNotIn('tool_fixture',json.dumps(data))
        self.assertTrue(any(r['event']=='classification' for r in data['events']))

    def test_incident_slot_evidence_excludes_resources_and_private_commands(self):
        from incident_evidence import queue_evidence
        state=self.root/'memcap'
        (state/'analytics').mkdir(parents=True)
        (state/'analytics/key').write_bytes(b'x'*32);(state/'analytics/key').chmod(0o600)
        (state/'queue').mkdir()
        fixture={'jobs':[
            {'id':'a'*32,'status':'waiting','admission':{'reason':'slots'},'command':'PRIVATE'},
            {'id':'b'*32,'status':'running','resource':'','label':'PRIVATE'},
            {'id':'c'*32,'status':'running','resource':'resource'}]}
        (state/'queue/jobs.json').write_text(json.dumps(fixture));(state/'queue/jobs.json').chmod(0o600)
        result=queue_evidence(state)
        self.assertEqual(result['status'],'observed')
        self.assertEqual(len(result['jobs'][0]['finite_slot_occupants']),1)
        self.assertNotIn('PRIVATE',json.dumps(result))
        self.assertNotIn('a'*32,json.dumps(result))
        (state/'queue/jobs.json').write_text('{invalid')
        self.assertEqual(queue_evidence(state)['status'],'unavailable')


if __name__ == '__main__':
    unittest.main()
