"""Pure scheduler integration: fake process measurements, no host workloads."""
import os
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
import subprocess
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'libexec'))
from scheduler import Scheduler
GIB=1048576


class LearningIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        (self.root/'main.go').write_text('package main\n'+'// fixture\n'*100)
        (self.root/'go.mod').write_text('module fixture.test\n')
        self.tool=self.root/'go';self.tool.write_text('fake compiler')
        self.queue=Scheduler(self.root/'queue',sampler=lambda:{},policy='adaptive',memory_gb=1)
        self.env=patch.dict(os.environ,MEMCAP_STATE_HOME=str(self.root/'state'),MC_DRY_RUN='1')
        self.env.start();self.addCleanup(self.env.stop)

    def demand(self,data):
        with patch('shutil.which',return_value=str(self.tool)):
            return self.queue.demand(['go','build','./...'],self.root,1,data)

    def test_compiler_prior_is_published_before_legacy_observer_reads_it(self):
        from compiler_profiles import record_profile
        data={'jobs':[],'compiler_profiles':{}}
        for _ in range(3):
            self.demand(data)
            record_profile(data['compiler_profiles'],self.queue.compiler_profile,GIB//8,True,time.time())
        key,amount=self.demand(data)
        self.assertEqual(amount,GIB//2)
        self.assertEqual(data['estimates'][key]['estimate_kb'],amount)
        job=dict(status='waiting',elastic=True,estimate_key=key,memory_kb=amount)
        data['jobs']=[job]
        self.queue.observe(data,dict(fault=False,pressure=1,monotonic=time.monotonic(),boot_id='fixture'))
        self.assertEqual(job['memory_kb'],GIB//2)
        data['estimates'][key]['estimate_kb']=3*GIB
        self.queue.observe(data,dict(fault=False,pressure=1,monotonic=time.monotonic(),boot_id='fixture'))
        self.assertEqual(job['memory_kb'],3*GIB)

    def test_owned_observation_does_not_advance_host_pressure_controller(self):
        job=self.job()
        data={'jobs':[job]}
        self.queue.controller={'last_sample':50,'healthy_since':40}
        self.queue.observe_owned(data,job,self.sample(100))
        self.queue.observe_owned(data,job,self.sample(100.25))
        self.assertTrue(job['owned_observation']['complete'])
        self.assertEqual(self.queue.controller,{'last_sample':50,'healthy_since':40})

    def job(self):
        return dict(id='a'*32,status='running',owner=99,group=10,elastic=True,
                    members={'10':'start'},footprint_members={'10':'start'},
                    memory_kb=GIB,reservation_kb=GIB,start_monotonic=100,
                    orphaned=False,learning_protocol=2)

    def sample(self,at):
        return dict(complete=True,peak_kb=GIB//8,identities={'10':'start'},
                    footprints={'10':GIB//8},at=at,missing=0,fault=0,duration_ms=1)

    def test_fixed_strict_and_orphan_floors_survive_owned_sampling(self):
        for change,policy in [({'elastic':False},'adaptive'),({'orphaned':True},'adaptive'),({},'strict')]:
            self.queue.policy=policy
            job={**self.job(),**change,'memory_kb':2*GIB,'reservation_kb':2*GIB}
            self.queue.observe_owned({'jobs':[job]},job,self.sample(200))
            self.assertGreaterEqual(job['reservation_kb'],2*GIB)

    def test_stale_host_child_map_cannot_poison_dedicated_learning(self):
        job=self.job();data={'jobs':[job]}
        self.queue.observe(data,dict(fault=False,pressure=1,footprints={},monotonic=101,boot_id='fixture'))
        self.assertNotIn('learning_incomplete',job)
        self.queue.observe_owned(data,job,self.sample(102))
        self.queue.observe_owned(data,job,self.sample(102.25))
        self.assertTrue(job['owned_observation']['complete'])
        self.queue.observe_owned(data,job,{**self.sample(102.5),'complete':False,'missing':1})
        self.assertFalse(job['owned_observation']['complete'])

    @unittest.skipUnless(sys.platform=='darwin','macOS physical-footprint smoke test')
    def test_short_owned_child_completes_with_native_observation_protocol(self):
        self.check_native_completion()

    @unittest.skipUnless(sys.platform=='darwin','macOS physical-footprint smoke test')
    def test_terminal_supervisor_stall_keeps_learning_incomplete(self):
        self.check_native_completion(terminal_gap=10)

    def check_native_completion(self, terminal_gap=0):
        import scheduler
        def healthy():
            return dict(cap_kb=16*GIB,tracked_kb=0,available_kb=16*GIB,pressure=1,fault=False,
                        footprints={},tracked_pids=[],monotonic=time.monotonic(),boot_id='fixture')
        events=[]
        gate=self.root/'observed'
        children=[]
        original_popen=subprocess.Popen
        def capture_child(*args,**kwargs):
            child=original_popen(*args,**kwargs)
            if kwargs.get('start_new_session'):
                children.append(child)
            return child
        with patch.object(scheduler,'sample_host',healthy),patch.object(scheduler,'pressure_allows',return_value=True),patch.object(scheduler,'append_event',side_effect=lambda directory,row:events.append(row)),patch.object(scheduler.subprocess,'Popen',side_effect=capture_child):
            queue=Scheduler(self.root/'native-queue',sampler=healthy,policy='adaptive',poll=.1)
            queue.measure=healthy
            observe=queue.observe_owned
            def observe_then_finish(data,job,sample):
                observe(data,job,sample)
                if job['owned_observation'].get('samples',0)>=3:
                    gate.touch()
                    # Fixture exits after observation; do not make a timed exit
                    # racing a live probe the success condition. Separate churn
                    # fixtures require uncertainty when a live read is missed.
                    children[0].wait(timeout=5)
                    job['owned_observation']['last'] -= terminal_gap
            queue.observe_owned=observe_then_finish
            code=('import time; from pathlib import Path; end=time.monotonic()+5\n'
                  f'while not Path({str(gate)!r}).exists() and time.monotonic()<end: time.sleep(.005)\n')
            result=queue.run([sys.executable,'-c',code],cwd=self.root,wait=10)
        self.assertEqual(result,0)
        completed=[r for r in events if r['event']=='completed']
        self.assertEqual(len(completed),1)
        self.assertEqual(completed[0]['learning_protocol'],2)
        self.assertGreaterEqual(completed[0]['learning_samples'],2)
        self.assertEqual(completed[0]['learning_complete'],int(terminal_gap==0),json.dumps(completed[0],sort_keys=True))
        self.assertGreater(completed[0]['peak_kb'],0)


if __name__=='__main__':unittest.main()
