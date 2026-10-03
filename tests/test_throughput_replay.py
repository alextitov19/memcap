"""Paired deterministic admission replay, not a whole-day productivity claim."""
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'libexec'))
from admission import decide
from compiler_profiles import predict,record_profile
GIB=1048576


def replay():
    profile=dict(key='fixture',source_bytes=1000,source_files=10)
    history={}
    for at in (100,101,102):record_profile(history,profile,GIB//8,True,at)
    policy=dict(mode='adaptive',allowed_pressure=(1,2),max_jobs=4,headroom_kb=GIB//2)
    result=[]
    for name,current,pressure in [('warm',profile,2),('edited',{**profile,'source_bytes':1100},2),
                                   ('grown',{**profile,'source_bytes':2000},2),('cold',{**profile,'key':'new'},2),
                                   ('red',profile,4)]:
        amount,_=predict(history,current,GIB,110)
        starts=[]
        for request in (GIB,amount):
            start=None
            for elapsed in (0,60,600):
                sample=dict(fault=False,pressure=pressure,monotonic=110+elapsed,tracked_kb=0,
                            cap_kb=20*GIB,available_kb=int((1.2 if elapsed<600 else 2)*GIB),footprints={},tracked_pids=[])
                decision=decide(policy,sample,dict(now=110+elapsed,healthy_since=100,last_start=100),[],dict(memory_kb=request,resource=''))
                if decision['allow']:
                    start=elapsed
                    break
            starts.append(start)
        result.append(dict(case=name,baseline_start_seconds=starts[0],candidate_start_seconds=starts[1]))
    return dict(synthetic=True,causal_production_claim=False,cases=result)


class ReplayTests(unittest.TestCase):
    def test_same_headroom_timeline_improves_only_supported_warm_predictions(self):
        cases={r['case']:r for r in replay()['cases']}
        for name in ('warm','edited'):
            self.assertEqual(cases[name]['baseline_start_seconds'],600)
            self.assertEqual(cases[name]['candidate_start_seconds'],0)
        for name in ('grown','cold'):
            self.assertEqual(cases[name]['candidate_start_seconds'],600)
        self.assertIsNone(cases['red']['baseline_start_seconds'])
        self.assertIsNone(cases['red']['candidate_start_seconds'])


if __name__=='__main__':
    print(json.dumps(replay(),sort_keys=True))
    unittest.main()
