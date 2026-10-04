"""Compiler prediction fixtures contain no real compilers or host processes."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'libexec'))
GIB = 1048576


class CompilerProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'go.mod').write_text('module example.test/fixture\n')
        (self.root / 'main.go').write_text('package main\n' + '// fixture\n' * 100)
        self.tool = self.root / 'go'
        self.tool.write_text('compiler identity fixture')
        self.history = {}

    def profile(self, workers=1, env=None, argv=None):
        from compiler_profiles import compiler_profile
        with patch('compiler_profiles.shutil.which', return_value=str(self.tool)):
            return compiler_profile(argv or ['go', 'build', './...'], self.root, workers, env or {})

    def train(self, n=3, complete=True, peak=GIB//8):
        from compiler_profiles import record_profile
        for i in range(n):
            record_profile(self.history, self.profile(), peak, complete, 100+i)

    def test_three_complete_builds_reuse_across_bounded_source_edits(self):
        from compiler_profiles import predict
        self.train()
        (self.root/'main.go').write_text('package main\n' + '// changed\n' * 100)
        estimate, reason = predict(self.history, self.profile(), GIB, 110)
        self.assertEqual(estimate, GIB//2)
        self.assertEqual(reason, 1)

    def test_incomplete_and_insufficient_evidence_never_lower(self):
        from compiler_profiles import predict
        self.train(2)
        self.train(4, complete=False)
        self.assertEqual(predict(self.history, self.profile(), GIB, 110)[0], GIB)

    def test_partial_large_peak_raises_and_cannot_be_erased_by_one_small_run(self):
        from compiler_profiles import predict, record_profile
        self.train()
        record_profile(self.history, self.profile(), 2*GIB, False, 110)
        record_profile(self.history, self.profile(), GIB//8, True, 111)
        self.assertGreaterEqual(predict(self.history, self.profile(), GIB, 112)[0], 3*GIB)

    def test_growth_expiry_dependency_tool_worker_and_environment_invalidate_lowering(self):
        from compiler_profiles import predict
        self.train()
        self.assertEqual(predict(self.history, self.profile(2), GIB, 110)[0], GIB)
        self.assertEqual(predict(self.history, self.profile(2), GIB, 110)[1], 6)
        self.assertEqual(predict(self.history, self.profile(env={'GOFLAGS':'-race'}), GIB, 110)[0], GIB)
        self.assertEqual(predict(self.history, self.profile(), GIB, 8*86400)[0], GIB)
        (self.root/'main.go').write_text('package main\n' + '// expanded\n' * 200)
        self.assertEqual(predict(self.history, self.profile(), GIB, 110)[0], GIB)
        (self.root/'go.mod').write_text('module different.example/fixture\n')
        self.assertEqual(predict(self.history, self.profile(), GIB, 110)[0], GIB)
        self.assertEqual(predict(self.history, self.profile(), GIB, 110)[1], 8)
        self.tool.write_text('new compiler identity')
        self.assertEqual(predict(self.history, self.profile(), GIB, 110)[0], GIB)

    def test_unsupported_execution_scope_is_not_reusable(self):
        for argv in (['go','test','./...'], ['bash','-c','go build ./...'],
                     ['go','build','-toolexec=custom','./...'],
                     ['go','build','../elsewhere'], ['docker','build','.']):
            self.assertIsNone(self.profile(argv=argv), argv)
        (self.root/'foreign.go').symlink_to(self.root/'main.go')
        self.assertIsNone(self.profile())

    def test_tsc_config_changes_invalidate_and_node_preloads_are_unsupported(self):
        from compiler_profiles import predict
        argv = ['tsc','--noEmit','-p','.']
        (self.root/'tsconfig.json').write_text('{"strict":true}')
        first = self.profile(argv=argv)
        self.assertIsNotNone(first)
        (self.root/'tsconfig.json').write_text('{"strict":false}')
        self.assertNotEqual(first['key'], self.profile(argv=argv)['key'])
        self.assertIsNone(self.profile(argv=argv, env={'NODE_OPTIONS':'--require /tmp/custom.js'}))
        self.assertEqual(predict(self.history, None, GIB, 110)[0], GIB)
        (self.root/'tsconfig.json').unlink()
        self.assertIsNone(self.profile(argv=argv))

    def test_plain_system_shell_preserves_scope_but_startup_and_external_inputs_do_not(self):
        self.assertIsNotNone(self.profile(argv=['/bin/bash','-c','go build ./...']))
        self.assertIsNone(self.profile(argv=['/bin/bash','-lc','go build ./...']))
        self.assertIsNone(self.profile(argv=['/bin/bash','-c','go build ./...'],env={'BASH_ENV':'startup.sh'}))
        (self.root/'go.mod').write_text('module fixture\nreplace example.test/x => ../other\n')
        self.assertIsNone(self.profile())

    def test_learned_request_fits_but_default_and_red_still_block(self):
        from compiler_profiles import predict
        from admission import decide
        self.train()
        learned, _ = predict(self.history, self.profile(), GIB, 110)
        policy = dict(mode='adaptive', allowed_pressure=(1,2), max_jobs=4, headroom_kb=GIB//2)
        sample = dict(fault=False, pressure=2, tracked_kb=0, cap_kb=20*GIB,
                      available_kb=int(1.2*GIB), footprints={}, tracked_pids=[], monotonic=110)
        ctl = dict(now=110, healthy_since=100, last_start=100)
        job = dict(resource='', memory_kb=learned)
        self.assertTrue(decide(policy,sample,ctl,[],job)['allow'])
        self.assertEqual(decide(policy,sample,ctl,[],{**job,'memory_kb':GIB})['reason'],'headroom')
        self.assertFalse(decide(policy,{**sample,'pressure':4},ctl,[],job)['allow'])
        self.assertFalse(decide(policy,{**sample,'available_kb':GIB//2},ctl,[],job)['allow'])

    def test_subdirectory_cannot_certify_unenumerated_parent_module_inputs(self):
        from compiler_profiles import compiler_profile
        subdir=self.root/'cmd'/'app'
        subdir.mkdir(parents=True)
        (subdir/'main.go').write_text('package main\n')
        with patch('compiler_profiles.shutil.which',return_value=str(self.tool)):
            self.assertIsNone(compiler_profile(['go','build','.'],subdir,1,{}))
        (self.root/'go.mod').unlink()
        with patch('compiler_profiles.shutil.which',return_value=str(self.tool)):
            self.assertIsNone(compiler_profile(['go','build','.'],self.root,1,{}))

    def test_unreadable_subtree_is_not_a_complete_source_envelope(self):
        def denied(*args, **kwargs):
            kwargs['onerror'](PermissionError('fixture unreadable source directory'))
            return iter(())
        with patch('compiler_profiles.os.walk',side_effect=denied):
            self.assertIsNone(self.profile())

    def test_large_unchanged_config_cannot_dilute_source_growth(self):
        from compiler_profiles import predict
        (self.root/'dependency-lock.json').write_text(' ' * 100000)
        self.train()
        (self.root/'main.go').write_text('package main\n' + '// expanded\n' * 200)
        estimate,reason=predict(self.history,self.profile(),GIB,110)
        self.assertEqual(estimate,GIB)
        self.assertEqual(reason,5)

    def test_large_source_envelope_uses_metadata_budget_without_reading_source_contents(self):
        from compiler_profiles import compiler_profile, predict, record_profile
        source=self.root/'generated.go'
        with source.open('wb') as stream:
            stream.truncate(9*1024*1024)
        original=Path.read_bytes
        def bounded_read(path):
            if path.suffix=='.go':
                self.fail('source content is not part of the reusable key; only its size envelope is needed')
            return original(path)
        with patch.object(Path,'read_bytes',bounded_read):
            profile=self.profile()
        self.assertIsNotNone(profile)
        self.assertGreater(profile['source_bytes'],9*1024*1024)
        history={}
        for at in (1,2,3): record_profile(history,profile,GIB//8,True,at)
        self.assertEqual(predict(history,profile,GIB,4)[0],GIB//2)
        with source.open('ab') as stream:
            stream.truncate(12*1024*1024)
        self.assertEqual(predict(history,self.profile(),GIB,4)[0],GIB)

    def test_config_payload_budget_and_symlinks_remain_rejected(self):
        config=self.root/'dependency.json'
        with config.open('wb') as stream: stream.truncate(9*1024*1024)
        self.assertIsNone(self.profile())
        config.unlink()
        (self.root/'linked.go').symlink_to(self.root/'main.go')
        self.assertIsNone(self.profile())


if __name__ == '__main__':
    unittest.main()
