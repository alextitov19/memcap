"""Bounded macOS read-only probe comparison, run inside live memcap admission.

Own fixture exits on its own; no signals, Docker commands or live state writes.
Output contains numeric timings only. This does not benchmark total agent work.
"""
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'libexec'))
from scheduler import processes,sample_host
from native_observer import usage
from job_observation import sample_job


def main():
    if sys.platform!='darwin':
        print(json.dumps({'available':False,'reason':'macOS-required'}))
        return
    with tempfile.TemporaryDirectory() as temporary:
        # Isolate every CLI state/config path. Sampling never reaches enforcement.
        os.environ.update(MEMCAP_STATE_HOME=temporary+'/state',MEMCAP_CONFIG_HOME=temporary+'/config',MC_DRY_RUN='1')
        host=[]
        for _ in range(3):
            start=time.monotonic();sample_host();host.append((time.monotonic()-start)*1000)
        child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(5)'],start_new_session=True)
        try:
            row=processes()[str(child.pid)]
            job=dict(owner=os.getpid(),group=child.pid,members={str(child.pid):row['start']})
            owned=[sample_job(job,processes,usage) for _ in range(5)]
        finally:
            child.wait(timeout=10)
        print(json.dumps(dict(available=True,host_probe_ms=host,
                              owned_probe_ms=[s['duration_ms'] for s in owned],
                              host_median_ms=statistics.median(host),
                              owned_median_ms=statistics.median(s['duration_ms'] for s in owned),
                              owned_complete=sum(s['complete'] for s in owned),
                              samples=len(owned),whole_session_speedup_claim=False),sort_keys=True))


if __name__=='__main__':main()
