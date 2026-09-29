"""Public-shaped reproductions; no real remote calls or live queue mutation."""
import concurrent.futures
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'libexec'))
from inspection import guarded_shell
from scheduler_policy import classify_shell


class OpenIssueTests(unittest.TestCase):
    def test_reported_remote_and_inspection_commands_do_not_reserve_workload_slots(self):
        for command in (
            'AWS_RETRY_MODE=standard AWS_MAX_ATTEMPTS=10 aws ssm send-command --parameters file:///tmp/fixture.json',
            'env AWS_RETRY_MODE=adaptive AWS_MAX_ATTEMPTS=3 aws ssm get-command-invocation --command-id fixture',
            'aws sso login --profile fixture --no-browser',
            'aws sso login --profile fixture',
            'benmore push --help',
            'benmore describe fixture tables --env dev 2>&1 | head -60',
            'benmore push static/compute/example.ts flows/example.yaml --app fixture --env dev',
            'unzip -p book.xlsx xl/sharedStrings.xml',
            'unzip -l book.epub',
            'command -v slack',
            'command -V aws',
            'gofmt -w internal/example.go',
            '''jq '{attempts:length,statuses:(group_by(.status)|map({status:.[0].status,count:length})),max_ms:(map(.elapsed_ms)|max)}' input.json''',
            'memcap cancel --help',
            'memcap cancel abcdef12',
            'gh issue close --help',
            'gh pr merge --help',
        ):
            with self.subTest(command=command):
                self.assertEqual(classify_shell(command)[0], 'light')

    def test_unknown_execution_and_unbounded_wrappers_still_queue(self):
        for command in (
            'AWS_RETRY_MODE=$(npm test) aws ssm send-command',
            'AWS_MAX_ATTEMPTS=999999999 aws ssm send-command',
            'BASH_ENV=/tmp/helper aws ssm send-command',
            'command npm test',
            'command -p npm test',
            'gh alias run --help',
            'gh extension exec helper --help',
            'benmore push --watch static/example.ts',
            'benmore push --exec helper static/example.ts',
            'benmore build fixture',
            'unzip -o archive.zip',
            'gofmt -w -unknown internal/example.go',
            "jq '[range(1000000000)] | group_by(.)' input.json",
        ):
            with self.subTest(command=command):
                self.assertEqual(classify_shell(command)[0], 'job')
                self.assertIsNone(guarded_shell(command, '/opt/memcap', 'fixture'))

    def environment(self, directory):
        return {**os.environ, 'HOME': str(directory), 'MEMCAP_CONFIG_HOME': str(directory/'config'),
                'MEMCAP_STATE_HOME': str(directory/'state'), 'MEMCAP_ROOT': str(ROOT), 'MC_DRY_RUN': '1'}

    def test_home_alias_and_file_line_loop_preserve_native_results(self):
        commands = (
            'M=~/notes; cat "$M/one.txt"; command -v cat',
            'for s in "one.txt:10" "two.txt:12"; do f=${s%%:*}; l=${s##*:}; echo "== $s"; sed -n "$((l-2)),$((l+2))p" "$f"; done',
        )
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)
            (directory/'notes').mkdir()
            (directory/'notes/one.txt').write_text('fixture\n')
            for name in ('one.txt','two.txt'):
                (directory/name).write_text(''.join(f'{n}\n' for n in range(1,21)))
            env=self.environment(directory)
            for command in commands:
                with self.subTest(command=command):
                    rewritten=guarded_shell(command,str(ROOT/'bin/memcap'),'fixture')
                    self.assertIsNotNone(rewritten)
                    expected=subprocess.run(['/bin/bash','-c',command],cwd=tmp,env=env,capture_output=True,text=True,timeout=10)
                    actual=subprocess.run(['/bin/bash','-c',rewritten],cwd=tmp,env=env,capture_output=True,text=True,timeout=10)
                    self.assertEqual((actual.returncode,actual.stdout,actual.stderr),(expected.returncode,expected.stdout,expected.stderr))
            self.assertFalse((directory/'state/memcap/queue/jobs.json').exists())

    def test_file_line_loop_cannot_execute_arithmetic_or_rebind_its_proven_values(self):
        for command in (
            'for s in "one.txt:OTHER"; do f=${s%%:*}; l=${s##*:}; sed -n "$((l+1))p" "$f"; done',
            'for s in "one.txt:10"; do f=${s%%:*}; l=${s##*:}; l=OTHER; sed -n "$((l+1))p" "$f"; done',
            'for s in *.txt; do f=${s%%:*}; l=${s##*:}; sed -n "$((l+1))p" "$f"; done',
            'for s in "one.txt:10"; do PATH=${s%%:*}; l=${s##*:}; cat "$PATH"; done',
            'M=~$(npm test); cat "$M/one.txt"',
            'PATH=~/bin; cat file',
        ):
            with self.subTest(command=command):
                self.assertIsNone(guarded_shell(command,'/opt/memcap','fixture'))

    def test_bounded_upload_loop_has_runtime_consumer_checks(self):
        command='for f in flows/a.yaml static/a.tsx; do benmore push "$f" --app fixture 2>&1 | tail -3; done'
        guarded=guarded_shell(command,str(ROOT/'bin/memcap'),'fixture')
        self.assertIsNotNone(guarded)
        self.assertIn('_inspect',guarded)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            fake = directory/'benmore'
            fake.write_text('#!/bin/bash\nprintf "%s\\n" "$@"\n')
            fake.chmod(0o700)
            env = self.environment(directory)
            env['PATH'] = str(directory) + os.pathsep + env['PATH']
            expected = subprocess.run(['/bin/bash', '-c', command], env=env, capture_output=True, text=True, timeout=10)
            actual = subprocess.run(['/bin/bash', '-c', guarded], env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual((actual.returncode, actual.stdout, actual.stderr),
                             (expected.returncode, expected.stdout, expected.stderr))
            self.assertFalse((directory/'state/memcap/queue/jobs.json').exists())

    def test_concurrent_hook_refresh_emits_full_guidance_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)
            env=self.environment(directory)
            payload={'hook_event_name':'PreToolUse','session_id':'fixture','cwd':tmp}
            def call(token='test:active', event='PreToolUse', session='fixture'):
                result=subprocess.run([sys.executable,str(ROOT/'libexec/agent_diagnostics.py'),'--session-token',token],
                    input=json.dumps({**payload,'hook_event_name':event,'session_id':session}),env=env,capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0)
                return result.stdout
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                outputs=list(pool.map(lambda _:call(),range(6)))
            self.assertEqual(sum('MUST report' in text for text in outputs),1)
            self.assertEqual(call(),'')
            self.assertIn('MUST report',call(event='SessionStart'))
            self.assertIn('MUST report',call(token='next:active'))
            self.assertIn('MUST report',call(session='another'))
            self.assertFalse((directory/'state/memcap/queue/jobs.json').exists())

    def test_session_start_refresh_survives_a_busy_receipt_lock(self):
        from agent_diagnostics import session_guidance
        with tempfile.TemporaryDirectory() as tmp:
            payload = {'hook_event_name': 'SessionStart', 'session_id': 'fixture'}
            with patch('agent_diagnostics.fcntl.flock', side_effect=BlockingIOError):
                self.assertIn('MUST report', session_guidance(payload, Path(tmp), 'test:active'))

    def test_repeated_queue_feedback_keeps_actionable_reporting_without_full_policy(self):
        from agent_diagnostics import guidance
        with tempfile.TemporaryDirectory() as tmp:
            payload={'hook_event_name':'PostToolUse','tool_name':'TaskOutput',
                     'tool_response':{'output':'memcap: queued abcdef12: preserving host memory headroom.'}}
            first=guidance(payload,Path(tmp))
            repeated=guidance(payload,Path(tmp),brief=True)
            self.assertLess(len(repeated),len(first)/2)
            self.assertIn('memcap report lightweight-queued',repeated)
            self.assertIn('memcap report polling-overhead',repeated)
            self.assertIn('60',repeated)

    def test_local_build_inside_remote_named_helper_remains_managed(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)
            script=directory/'deploy.sh'
            script.write_text('#!/bin/bash\nset -e\ngo build ./cmd/check\naws ssm send-command --instance-ids fixture\n')
            self.assertIsNone(guarded_shell('bash deploy.sh','/opt/memcap','fixture',tmp))

    def test_failed_measured_runs_raise_estimates_without_success_credit(self):
        from scheduler import Scheduler, GIB
        for result in (7,-9):
            with self.subTest(result=result),tempfile.TemporaryDirectory() as tmp:
                queue=Scheduler(Path(tmp)/'queue',sampler=lambda: {},policy='strict')
                table={str(os.getpid()):dict(ppid=1,uid=os.getuid(),group=0,start='fixture')}
                def launched(argv,cwd,job,data):
                    job.update(status='running',started=time.time(),sample_count=2,
                               learning_incomplete=False,observed_peak_kb=12*GIB,estimate_key='fixture')
                    queue.save(data)
                    return SimpleNamespace(poll=lambda: result)
                with patch('scheduler.processes',return_value=table),patch.object(queue,'admissible',return_value=(True,'')),\
                     patch.object(queue,'next_waiter',side_effect=lambda jobs,*args:jobs[0]['id']),\
                     patch.object(queue,'launch',side_effect=launched):
                    self.assertEqual(queue.run(['fixture-job'],wait=1),result if result>=0 else 128-result)
                learned=json.loads((Path(tmp)/'queue/jobs.json').read_text()).get('estimates',{}).get('fixture',{})
                self.assertEqual(learned.get('estimate_kb'),15*GIB)
                self.assertEqual(learned.get('complete_runs',0),0)

    def test_recently_observed_large_estimate_survives_cache_churn(self):
        from scheduler import Scheduler, GIB
        with tempfile.TemporaryDirectory() as tmp:
            queue=Scheduler(Path(tmp)/'queue',sampler=lambda: {},policy='strict')
            with queue.locked() as data:
                data['estimates']={'recent':{'estimate_kb':GIB},**{f'old-{n}':{'estimate_kb':GIB} for n in range(255)}}
                queue.save(data)
            table={str(os.getpid()):dict(ppid=1,uid=os.getuid(),group=0,start='fixture')}
            for key in ('recent','new'):
                def launched(argv,cwd,job,data):
                    job.update(status='running',started=time.time(),sample_count=1,
                               learning_incomplete=False,observed_peak_kb=12*GIB,estimate_key=key)
                    queue.save(data)
                    return SimpleNamespace(poll=lambda:0)
                with patch('scheduler.processes',return_value=table),patch.object(queue,'admissible',return_value=(True,'')),\
                     patch.object(queue,'next_waiter',side_effect=lambda jobs,*args:jobs[0]['id']),\
                     patch.object(queue,'launch',side_effect=launched):
                    self.assertEqual(queue.run(['fixture-job'],wait=1),0)
            learned=json.loads((Path(tmp)/'queue/jobs.json').read_text())['estimates']
            self.assertEqual(len(learned),256)
            self.assertEqual(learned.get('recent',{}).get('estimate_kb'),15*GIB)
            self.assertNotIn('old-0',learned)


if __name__=='__main__':
    unittest.main()
