"""Exercise real fast-forward updates against a disposable local Git remote."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from updater import prepare_update


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='planner-update-test-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.remote = root / 'remote.git'
        self.author = root / 'author'
        self.install = root / 'install'
        self.git(root, 'init', '--bare', str(self.remote))
        self.git(root, 'clone', str(self.remote), str(self.author))
        self.git(self.author, 'config', 'user.email', 'test@example.invalid')
        self.git(self.author, 'config', 'user.name', 'Planner tests')
        self.git(self.author, 'checkout', '-b', 'planner-test')
        (self.author / 'requirements.txt').write_text('flask>=3.0\n')
        (self.author / 'app.py').write_text('from flask import Flask\napp=Flask(__name__)\n@app.route("/planner")\ndef ready(): return "ready"\n@app.route("/settings")\ndef settings(): return "settings"\n')
        self.commit('initial')
        self.git(root, 'clone', '-b', 'planner-test', str(self.remote), str(self.install))
        (self.install / 'data').mkdir()
        (self.install / 'data/plans.json').write_text('[{"id":"keep-me"}]')
        self.old_head = self.git(self.install, 'rev-parse', 'HEAD')

    @staticmethod
    def git(root, *args):
        return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, message):
        self.git(self.author, 'add', '.')
        self.git(self.author, 'commit', '-m', message)
        self.git(self.author, 'push', 'origin', 'planner-test')

    def local_commit(self):
        self.git(self.install, 'config', 'user.email', 'test@example.invalid')
        self.git(self.install, 'config', 'user.name', 'Planner tests')
        (self.install / 'linux_support.py').write_text('VALUE=1\n')
        self.git(self.install, 'add', 'linux_support.py')
        self.git(self.install, 'commit', '-m', 'local Linux support')
        return self.git(self.install, 'rev-parse', 'HEAD')

    def test_unpublished_local_commit_reports_ahead_without_changing_code(self):
        head = self.local_commit()
        result = prepare_update(self.install)
        self.assertFalse(result['updated'])
        self.assertIn('ahead of GitHub', result['message'])
        self.assertEqual(self.git(self.install, 'rev-parse', 'HEAD'), head)
        self.assertEqual((self.install / 'data/plans.json').read_text(), '[{"id":"keep-me"}]')

    def test_diverged_branches_explain_merge_without_changing_code(self):
        head = self.local_commit()
        (self.author / 'mac_feature.py').write_text('VALUE=2\n')
        self.commit('new Mac feature')
        with self.assertRaisesRegex(RuntimeError, 'both have new changes'):
            prepare_update(self.install)
        self.assertEqual(self.git(self.install, 'rev-parse', 'HEAD'), head)

    def test_valid_update_preserves_local_data_and_branch(self):
        (self.author / 'new_module.py').write_text('VALUE=1\n')
        self.commit('valid update')
        result = prepare_update(self.install)
        self.assertTrue(result['updated'])
        self.assertEqual(self.git(self.install, 'branch', '--show-current'), 'planner-test')
        self.assertEqual((self.install / 'data/plans.json').read_text(), '[{"id":"keep-me"}]')
        self.assertFalse(prepare_update(self.install)['updated'])

    def test_broken_update_is_rejected_before_checkout_changes(self):
        (self.author / 'app.py').write_text('this is not valid python !!\n')
        self.commit('broken update')
        with self.assertRaisesRegex(RuntimeError, 'startup check'):
            prepare_update(self.install)
        self.assertEqual(self.git(self.install, 'rev-parse', 'HEAD'), self.old_head)

    def test_missing_dependency_is_rejected_before_checkout_changes(self):
        (self.author / 'requirements.txt').write_text('nonexistent-planner-test-package-498392==1.0\n')
        self.commit('dependency change')
        with self.assertRaisesRegex(RuntimeError, 'dependencies'):
            prepare_update(self.install)
        self.assertEqual(self.git(self.install, 'rev-parse', 'HEAD'), self.old_head)

    def test_local_edits_are_never_stashed_or_overwritten(self):
        (self.install / 'app.py').write_text('# user edits\n')
        with self.assertRaisesRegex(RuntimeError, 'Local code changes'):
            prepare_update(self.install)
        self.assertEqual((self.install / 'app.py').read_text(), '# user edits\n')


if __name__ == '__main__':
    unittest.main()
