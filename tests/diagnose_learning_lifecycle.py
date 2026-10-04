"""Bounded natural-exit reproduction. Run through live admission; no signals.

All scheduler/config state is temporary. Emits only synthetic numeric evidence.
Unlike the historical gated fixture, child exit is not synchronized to probes.
"""
import json
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'libexec'))
import scheduler
from scheduler import Scheduler
GIB=1048576


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    results=[]
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp)
        with patch.dict(os.environ,MEMCAP_STATE_HOME=str(root/'state'),MEMCAP_CONFIG_HOME=str(root/'config'),MC_DRY_RUN='1'):
            def healthy():
                return dict(cap_kb=16*GIB,tracked_kb=0,available_kb=16*GIB,pressure=1,fault=False,
                            footprints={},tracked_pids=[],monotonic=time.monotonic(),boot_id='fixture')
            for churn in (False,True):
                for iteration in range(4):
                    events=[];observations=[]
                    with patch.object(scheduler,'sample_host',healthy),patch.object(scheduler,'pressure_allows',return_value=True),patch.object(scheduler,'append_event',side_effect=lambda directory,row:events.append(row)):
                        queue=Scheduler(root/f'queue-{churn}-{iteration}',sampler=healthy,policy='adaptive',poll=.1)
                        queue.measure=healthy
                        original=queue.observe_owned
                        def observe(data,job,sample):
                            observations.append(dict(complete=sample['complete'],missing=sample.get('missing',0),
                                sampled=len(sample.get('identities',{})),fresh_members=len(job.get('footprint_members',job['members'])),
                                fault=sample.get('fault',0),reason=sample.get('reason',0)))
                            original(data,job,sample)
                        queue.observe_owned=observe
                        code='import time; time.sleep(1.2)'
                        if churn:
                            code=('import subprocess,sys,time\n'
                                  'for _ in range(5):\n'
                                  ' subprocess.run([sys.executable,"-c","import time; time.sleep(.12)"],check=True)\n'
                                  'time.sleep(.5)\n')
                        outcome=queue.run([sys.executable,'-c',code],cwd=root,wait=10)
                    completed=[r for r in events if r['event']=='completed'][0]
                    results.append(dict(churn=churn,iteration=iteration,outcome=outcome,
                                        completed=completed,observations=observations))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(results,indent=2)+'\n')
    print(json.dumps(dict(runs=len(results),complete=sum(r['completed']['learning_complete'] for r in results),
                          stable_complete=sum(r['completed']['learning_complete'] for r in results if not r['churn']),
                          churn_complete=sum(r['completed']['learning_complete'] for r in results if r['churn']))))


if __name__=='__main__':main()
