"""Static compiler wrapper resolution; fake tools, no package/script execution."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'libexec'))


class CompilerCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.app=self.root/'app';self.app.mkdir()
        (self.app/'go.mod').write_text('module fixture.test\n')
        (self.app/'main.go').write_text('package main\n')
        (self.app/'tsconfig.json').write_text('{}')
        (self.app/'main.ts').write_text('export const answer=42;\n')
        self.tool=self.root/'compiler';self.tool.write_text('fake compiler')

    def profile(self,argv,env=None):
        from compiler_profiles import compiler_profile
        def lookup(tool,path=None):
            return tool if '/' in tool and Path(tool).is_file() else str(self.tool)
        with patch('compiler_profiles.shutil.which',side_effect=lookup):
            return compiler_profile(argv,self.root,1,env or {})

    def test_literal_directory_environment_and_go_directory_flag_reach_profile(self):
        for argv in (['go','-C','app','build','.'],
                     ['/usr/bin/env','GOMAXPROCS=2','go','-C','app','build','.'],
                     ['/bin/bash','-c','cd app && GOMAXPROCS=2 go build .']):
            with self.subTest(argv=argv):
                self.assertIsNotNone(self.profile(argv))
        first=self.profile(['/usr/bin/env','GOMAXPROCS=2','go','-C','app','build','.'])
        second=self.profile(['/usr/bin/env','GOMAXPROCS=3','go','-C','app','build','.'])
        self.assertNotEqual(first['key'],second['key'])

    def test_compound_or_startup_injection_never_certifies_compiler_only_scope(self):
        for text in ('cd app && go build . && python allocate.py',
                     'cd app; go build .', 'cd "$APP" && go build .',
                     'cd app && BASH_ENV=custom go build .',
                     'cd app && go build . | cat'):
            self.assertIsNone(self.profile(['/bin/bash','-c',text]),text)
        self.assertIsNone(self.profile(['/bin/bash','-lc','cd app && go build .']))
        self.assertIsNone(self.profile(['/bin/bash','-c','cd app && go build .'],
                                       {'BASH_FUNC_go%%':'() { custom_compiler; }'}))
        self.assertIsNone(self.profile(['/usr/bin/env','LD_PRELOAD=custom','go','-C','app','build','.']))

    def test_cdpath_cannot_redirect_profile_to_different_compiler_inputs(self):
        foreign=self.root/'foreign';(foreign/'app').mkdir(parents=True)
        (foreign/'app'/'go.mod').write_text('module foreign.test\n')
        (foreign/'app'/'main.go').write_text('package main\n')
        argv=['/bin/bash','-c','cd app && go build .']
        self.assertIsNotNone(self.profile(argv))
        self.assertIsNone(self.profile(argv,{'CDPATH':str(foreign)}))

    def package(self,scripts):
        (self.app/'package.json').write_text(json.dumps({'scripts':scripts}))
        tool=self.app/'node_modules'/'.bin'/'tsc';tool.parent.mkdir(parents=True,exist_ok=True)
        tool.write_text('compiler fixture')

    def test_local_package_compiler_script_is_bounded_and_content_keyed(self):
        self.package({'typecheck':'tsc --noEmit'})
        argv=['/bin/sh','-c','cd app && npm run typecheck']
        first=self.profile(argv)
        self.assertIsNotNone(first)
        self.package({'typecheck':'tsc --noEmit --strict'})
        self.assertNotEqual(first['key'],self.profile(argv)['key'])
        for scripts in ({'typecheck':'tsc --noEmit','pretypecheck':'node heavy.js'},
                        {'typecheck':'tsc --noEmit','posttypecheck':'node heavy.js'},
                        {'typecheck':'tsc --noEmit && node heavy.js'},
                        {'typecheck':'node custom.js'}):
            self.package(scripts)
            self.assertIsNone(self.profile(argv))

    def test_package_custom_shell_and_preloads_are_not_supported(self):
        self.package({'typecheck':'tsc --noEmit'})
        argv=['/bin/sh','-c','cd app && npm run typecheck']
        self.assertIsNone(self.profile(argv,{'npm_config_script_shell':'custom'}))
        self.assertIsNone(self.profile(argv,{'NODE_OPTIONS':'--require custom.js'}))
        (self.app/'.npmrc').write_text('script-shell=custom\n')
        self.assertIsNone(self.profile(argv))

    def test_explicit_package_manager_identity_uses_invoked_executable(self):
        self.package({'typecheck':'tsc --noEmit'})
        installed=self.root/'other'/'bin';installed.mkdir(parents=True)
        npm=installed/'npm';npm.write_text('first npm')
        argv=['/bin/sh','-c',f'cd app && {npm} run typecheck']
        first=self.profile(argv)
        self.assertIsNotNone(first)
        npm.write_text('changed actual npm implementation')
        self.assertNotEqual(first['key'],self.profile(argv)['key'])

    def test_scope_diagnostics_distinguish_wrapper_from_input_rejection(self):
        from compiler_profiles import compiler_profile
        diagnostics={}
        compiler_profile(['docker','build','.'],self.root,1,{},diagnostics=diagnostics)
        self.assertEqual(diagnostics['reason'],1)
        diagnostics={}
        compiler_profile(['/bin/sh','-lc','go build .'],self.root,1,{},diagnostics=diagnostics)
        self.assertEqual(diagnostics['reason'],2)


if __name__=='__main__':unittest.main()
