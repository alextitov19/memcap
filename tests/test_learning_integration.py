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

    def test_unavailable_compiler_reuse_preserves_exact_learning_and_growth(self):
        from compiler_profiles import record_profile
        for argv in (['go', 'test', './...'], ['go', 'build', './...']):
            for complete_runs in (0, 1):
                with self.subTest(argv=argv, compiler_runs=complete_runs):
                    data={'jobs':[], 'compiler_profiles':{}}
                    with patch('shutil.which',return_value=str(self.tool)):
                        key,_=self.queue.demand(argv,self.root,1,data)
                        data['estimates']={key:{'estimate_kb':GIB//2,'complete_runs':8,'peaks_kb':[GIB//8]*8}}
                        if complete_runs:
                            record_profile(data['compiler_profiles'],self.queue.compiler_profile,GIB//8,True,time.time())
                        _,amount=self.queue.demand(argv,self.root,1,data)
                        self.assertEqual(amount,GIB//2)
                        record_profile(data['compiler_profiles'],self.queue.compiler_profile,2*GIB,False,time.time())
                        _,amount=self.queue.demand(argv,self.root,1,data)
                        self.assertGreaterEqual(amount,2*GIB if self.queue.compiler_profile else GIB//2)
                        data['estimates'][key]['estimate_kb']=3*GIB
                        self.assertGreaterEqual(self.queue.demand(argv,self.root,1,data)[1],3*GIB)

    def test_wrapped_compiler_reuses_three_complete_runs_after_source_edit(self):
        from compiler_profiles import record_profile
        from admission import decide
        data={'jobs':[], 'compiler_profiles':{}}
        argv=['/bin/sh','-c','GOMAXPROCS=1 go build ./...']
        # Bats exports shell functions. Those correctly make a real shell
        # wrapper ineligible for bounded prediction; this fixture models a
        # plain compiler environment, independently of the test launcher.
        compiler_env = dict(HOME=str(self.root), PATH=os.defpath, GOENV='off',
                            GOWORK='off', MC_DRY_RUN='1',
                            MEMCAP_CONFIG_HOME=str(self.root/'config'),
                            MEMCAP_STATE_HOME=str(self.root/'state'))
        with patch.dict(os.environ, compiler_env, clear=True), patch('shutil.which',return_value=str(self.tool)):
            for _ in range(3):
                self.queue.demand(argv,self.root,1,data)
                self.assertIsNotNone(self.queue.compiler_profile, self.queue.compiler_scope_reason)
                record_profile(data['compiler_profiles'],self.queue.compiler_profile,GIB//8,True,time.time())
            (self.root/'main.go').write_text('package main\n'+'// changed\n'*100)
            _,request=self.queue.demand(argv,self.root,1,data)
        self.assertEqual(request,GIB//2)
        sample=dict(fault=False,pressure=2,tracked_kb=0,cap_kb=20*GIB,available_kb=int(1.2*GIB),
                    footprints={},tracked_pids=[],monotonic=110)
        policy=dict(mode='adaptive',allowed_pressure=(1,2),max_jobs=4,headroom_kb=GIB//2)
        ctl=dict(now=110,healthy_since=100,last_start=100)
        self.assertTrue(decide(policy,sample,ctl,[],dict(memory_kb=request,resource=''))['allow'])
        self.assertFalse(decide(policy,{**sample,'pressure':4},ctl,[],dict(memory_kb=request,resource=''))['allow'])

    def test_owned_observation_does_not_advance_host_pressure_controller(self):
        job=self.job()
        data={'jobs':[job]}
        self.queue.controller={'last_sample':50,'healthy_since':40}
        self.queue.observe_owned(data,job,self.sample(100))
        self.queue.observe_owned(data,job,self.sample(100.25))
        self.assertTrue(job['owned_observation']['complete'])
        self.assertEqual(self.queue.controller,{'last_sample':50,'healthy_since':40})

    def test_complete_sample_survives_exit_before_registry_refresh(self):
        job=self.job()
        self.queue.observe_owned({'jobs':[job]},job,self.sample(100))
        # The paired probe measured the same process completely. It exited
        # before the later registry refresh; this is not a missing live read.
        job['members']={};job['footprint_members']={}
        self.queue.observe_owned({'jobs':[job]},job,self.sample(100.25))
        self.assertTrue(job['owned_observation']['complete'])

    def test_refresh_birth_still_prevents_reservation_reduction(self):
        job=self.job()
        job['members']['11']='new';job['footprint_members']['11']='new'
        self.queue.observe_owned({'jobs':[job]},job,self.sample(200))
        self.assertFalse(job['measurement_complete'])
        self.assertGreaterEqual(job['reservation_kb'],GIB)

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

    @unittest.skipUnless(sys.platform=='darwin','macOS physical-footprint smoke test')
    def test_exit_between_first_poll_and_empty_probe_preserves_prior_evidence(self):
        self.check_native_completion(terminal_empty=True)

    @unittest.skipUnless(sys.platform=='darwin','macOS physical-footprint smoke test')
    def test_empty_terminal_probe_does_not_erase_fault_or_stale_observation(self):
        self.check_native_completion(terminal_empty=True, terminal_fault=True)
        self.check_native_completion(terminal_empty=True, terminal_gap=10)

    def check_native_completion(self, terminal_gap=0, terminal_empty=False, terminal_fault=False):
        import scheduler
        import job_observation
        def healthy():
            return dict(cap_kb=16*GIB,tracked_kb=0,available_kb=16*GIB,pressure=1,fault=False,
                        footprints={},tracked_pids=[],monotonic=time.monotonic(),boot_id='fixture')
        events=[]
        gate=self.root/'observed'
        gate.unlink(missing_ok=True)
        children=[]
        original_popen=subprocess.Popen
        original_sample=job_observation.sample_job
        def terminal_probe(job,*args,**kwargs):
            if terminal_empty and job.get('owned_observation',{}).get('samples',0)>=3:
                gate.touch()
                children[0].wait(timeout=5)
                return dict(complete=False,identities={},footprints={},peak_kb=0,
                            at=time.monotonic(),missing=0,fault=int(terminal_fault),reasons={'anchor':1})
            return original_sample(job,*args,**kwargs)
        def capture_child(*args,**kwargs):
            child=original_popen(*args,**kwargs)
            if kwargs.get('start_new_session'):
                children.append(child)
            return child
        with patch.object(scheduler,'sample_host',healthy),patch.object(scheduler,'pressure_allows',return_value=True),patch.object(scheduler,'append_event',side_effect=lambda directory,row:events.append(row)),patch.object(scheduler.subprocess,'Popen',side_effect=capture_child),patch.object(job_observation,'sample_job',side_effect=terminal_probe):
            queue=Scheduler(self.root/'native-queue',sampler=healthy,policy='adaptive',poll=.1)
            queue.measure=healthy
            observe=queue.observe_owned
            def observe_then_finish(data,job,sample):
                observe(data,job,sample)
                if terminal_empty and terminal_gap and job['owned_observation'].get('samples',0)==3:
                    job['owned_observation']['last'] -= terminal_gap
                if not terminal_empty and job['owned_observation'].get('samples',0)>=3:
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
        admitted = next(r for r in events if r['event'] == 'admitted')
        self.assertGreaterEqual(admitted['queue_wait_elapsed_ms'], 0)
        self.assertGreaterEqual(completed[0]['runtime_elapsed_ms'], completed[0]['runtime_awake_ms'] - 10)
        self.assertLess(completed[0]['runtime_sleep_ms'], 1000)
        self.assertEqual(completed[0]['learning_protocol'],2)
        self.assertGreaterEqual(completed[0]['learning_samples'],2)
        self.assertEqual(completed[0]['learning_complete'],int(terminal_gap==0 and not terminal_fault),json.dumps(completed[0],sort_keys=True))
        if terminal_empty and not terminal_fault:
            self.assertEqual(completed[0]['observation_terminal_empty'],1)
        self.assertGreater(completed[0]['peak_kb'],0)


if __name__=='__main__':unittest.main()
