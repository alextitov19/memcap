"""Shared sample compatibility is independent of a caller's private identity."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'libexec'))


class SamplingContextTests(unittest.TestCase):
    def test_busy_sampler_rereads_newly_published_fresh_sample(self):
        from scheduler_metrics import shared_sample
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'sample.json').write_text(json.dumps({'key':'same','sample':{'monotonic':90}}))
            def another_sampler_publishes(*args):
                (root/'sample.json').write_text(json.dumps({'key':'same','sample':{'monotonic':99.5,'pressure':2}}))
                raise BlockingIOError()
            with patch('scheduler_metrics.time.monotonic',return_value=100),patch('scheduler_metrics.fcntl.flock',side_effect=another_sampler_publishes):
                sample=shared_sample(root,'same',lambda:self.fail('duplicate probe'))
            self.assertFalse(sample.get('busy',False))
            self.assertEqual(sample['monotonic'],99.5)

    def test_busy_sampler_never_reuses_newly_published_incompatible_or_stale_sample(self):
        from scheduler_metrics import shared_sample
        for key,stamp in (('other',99.5),('same',98)):
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                def publish(*args):
                    (root/'sample.json').write_text(json.dumps({'key':key,'sample':{'monotonic':stamp}}))
                    raise BlockingIOError()
                with patch('scheduler_metrics.time.monotonic',return_value=100),patch('scheduler_metrics.fcntl.flock',side_effect=publish):
                    sample=shared_sample(root,'same',lambda:self.fail('duplicate probe'))
                self.assertTrue(sample['busy'])

    def test_session_metadata_shares_but_config_and_probe_controls_do_not(self):
        from scheduler_metrics import measurement_signature
        with tempfile.TemporaryDirectory() as directory:
            config=Path(directory)/'memcap.conf'
            config.write_text('TOTAL_BUDGET_GB=20\n')
            env={'QUEUE_POLICY':'adaptive','MEMCAP_SESSION_KEY':'one','MEMCAP_QUEUE_LEASE':'lease1'}
            a=measurement_signature(config,env,'source1')
            b=measurement_signature(config,{**env,'MEMCAP_SESSION_KEY':'two','MEMCAP_QUEUE_LEASE':'lease2'},'source1')
            self.assertEqual(a,b)
            for change in ({'MC_NO_TOP':'1'},{'QUEUE_POLICY':'strict'},{'MC_UNKNOWN_FUTURE_PROBE':'on'}):
                self.assertNotEqual(a,measurement_signature(config,{**env,**change},'source1'))
            self.assertNotEqual(a,measurement_signature(config,env,'source2'))
            config.write_text('TOTAL_BUDGET_GB=18\n')
            self.assertNotEqual(a,measurement_signature(config,env,'source1'))

    def test_stale_and_busy_are_distinguishable_and_never_allow(self):
        from admission import decide
        policy=dict(mode='adaptive',allowed_pressure=(1,2),max_jobs=4,headroom_kb=524288)
        sample=dict(fault=False,pressure=1,tracked_kb=0,cap_kb=20*1048576,
                    available_kb=10*1048576,footprints={},tracked_pids=[],monotonic=1)
        controller=dict(now=4,healthy_since=0)
        job=dict(memory_kb=1048576,resource='')
        stale=decide(policy,sample,controller,[],job)
        busy=decide(policy,{**sample,'busy':True},controller,[],job)
        self.assertFalse(stale['allow'])
        self.assertFalse(busy['allow'])
        self.assertEqual(stale['sampling_reason'],2)
        self.assertEqual(busy['sampling_reason'],1)


if __name__=='__main__': unittest.main()
