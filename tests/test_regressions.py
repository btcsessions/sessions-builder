"""Offline regression checks: all persistence uses disposable synthetic data."""
import atexit
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
import zipfile
from concurrent.futures import ThreadPoolExecutor

_ROOT = tempfile.TemporaryDirectory(prefix='planner-tests-')
atexit.register(_ROOT.cleanup)
os.environ['PLANNER_DATA_DIR'] = _ROOT.name
os.environ['PLANNER_SKIP_BACKGROUND'] = '1'

import app as planner
import storage
import sync
import llm
from plan_changes import validate_change
from updater import patch_installed_launcher


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_atomic_replace_failure_keeps_previous_data(self):
        path = self.root / 'plans.json'
        storage.write_json(path, [{'id': 'old'}])
        replace = os.replace
        def fail_new(src, dst):
            if Path(dst) == path:
                raise OSError('simulated disk error')
            return replace(src, dst)
        with patch('storage.os.replace', side_effect=fail_new):
            with self.assertRaises(OSError):
                storage.write_json(path, [{'id': 'new'}])
        self.assertEqual(storage.read_json(path), [{'id': 'old'}])

    def test_corrupt_file_recovers_and_preserves_damaged_copy(self):
        path = self.root / 'plans.json'
        storage.write_json(path, [{'id': 'previous'}])
        storage.write_json(path, [{'id': 'current'}])
        path.write_text('{')
        self.assertEqual(storage.read_json(path), [{'id': 'previous'}])
        self.assertTrue(list(self.root.glob('plans.json.corrupt-*')))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_invalid_restore_does_not_replace_any_file(self):
        storage.write_json(self.root / 'settings.json', {'channel_name': 'Local'})
        with self.assertRaises(ValueError):
            storage.restore_documents(self.root, {'settings.json': {'channel_name': 'Remote'}, 'plans.json': {}})
        self.assertEqual(storage.read_json(self.root / 'settings.json')['channel_name'], 'Local')
        self.assertFalse((self.root / '.restore-pending.json').exists())

    def test_failed_multi_file_restore_rolls_back(self):
        storage.write_json(self.root / 'settings.json', {'channel_name': 'Local'})
        storage.write_json(self.root / 'plans.json', [{'id': 'old'}])
        writer = storage.write_json
        def fail_plans(path, value):
            if Path(path).name == 'plans.json':
                raise OSError('disk failure')
            writer(path, value)
        with patch('storage.write_json', side_effect=fail_plans):
            with self.assertRaises(OSError):
                storage.restore_documents(self.root, {'settings.json': {'channel_name': 'Remote'}, 'plans.json': []})
        self.assertEqual(storage.read_json(self.root / 'settings.json')['channel_name'], 'Local')
        self.assertEqual(storage.read_json(self.root / 'plans.json'), [{'id': 'old'}])

    def test_interrupted_restore_recovers_at_next_start(self):
        storage.atomic_text(self.root / '.restore-pending.json', json.dumps({'settings.json': '{"channel_name":"Before"}', 'plans.json': None}))
        storage.write_json(self.root / 'settings.json', {'channel_name': 'Partial'})
        storage.write_json(self.root / 'plans.json', [])
        storage.recover_restore(self.root)
        self.assertEqual(storage.read_json(self.root / 'settings.json')['channel_name'], 'Before')
        self.assertFalse((self.root / 'plans.json').exists())


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dir_patch = patch.object(sync, 'DATA_DIR', str(self.root))
        self.dir_patch.start()
        self.addCleanup(self.dir_patch.stop)
        self.settings = {'sync_gist_id': 'synthetic-gist', 'sync_github_token': 'synthetic-token',
                         'sync_password': 'test password', 'anthropic_key': 'synthetic-key',
                         'backend_token': 'local-backend', 'channel_name': 'Local'}
        storage.write_json(self.root / 'settings.json', self.settings)
        storage.write_json(self.root / 'oauth_token.json', {'token': 'synthetic-access', 'refresh_token': 'synthetic-refresh'})
        sync._last_sync_error = ''

    def test_encrypted_backup_has_no_plaintext_credentials_or_plans(self):
        storage.write_json(self.root / 'plans.json', [{'id': 'old'}])
        with patch.object(sync, '_gist_api', return_value={'id': 'synthetic'}) as api:
            self.assertTrue(sync.push_to_gist())
        files = api.call_args.args[3]['files']
        for name in ['settings.json', 'oauth_token.json', 'plans.json']:
            self.assertIsNone(files[name])
            self.assertIn(name + '.enc', files)
        settings = json.loads(sync._decrypt(files['settings.json.enc']['content'], 'test password'))
        self.assertNotIn('sync_github_token', settings)
        self.assertNotIn('backend_token', settings)
        self.assertEqual(settings['anthropic_key'], 'synthetic-key')
        self.assertNotIn('synthetic-refresh', json.dumps(files))

    def test_no_password_never_pushes_oauth_token(self):
        self.settings['sync_password'] = ''
        storage.write_json(self.root / 'settings.json', self.settings)
        with patch.object(sync, '_gist_api', return_value={'id': 'synthetic'}) as api:
            self.assertTrue(sync.push_to_gist())
        files = api.call_args.args[3]['files']
        self.assertIsNone(files['oauth_token.json'])
        self.assertNotIn('synthetic-key', json.dumps(files))
        self.assertNotIn('synthetic-refresh', json.dumps(files))

    def test_legacy_restore_preserves_local_secrets_and_snapshots_old_data(self):
        remote = {'files': {'settings.json': {'content': '{"channel_name":"Remote"}'}}}
        with patch.object(sync, '_gist_api', return_value=remote):
            self.assertTrue(sync.pull_from_gist())
        current = storage.read_json(self.root / 'settings.json')
        self.assertEqual(current['anthropic_key'], 'synthetic-key')
        self.assertEqual(current['backend_token'], 'local-backend')
        self.assertEqual(current['channel_name'], 'Remote')
        self.assertTrue(list((self.root / 'recovery').glob('restore-*.json')))

    def test_wrong_password_aborts_whole_restore(self):
        remote = {'files': {'plans.json': {'content': '[{"id":"remote"}]'},
                            'settings.json.enc': {'content': sync._encrypt('{}', 'wrong')}}}
        with patch.object(sync, '_gist_api', return_value=remote):
            self.assertFalse(sync.pull_from_gist())
        self.assertFalse((self.root / 'plans.json').exists())
        self.assertIn('decrypt', sync.get_last_sync_error())

    def test_edits_during_download_cancel_restore(self):
        def fetch(*args):
            storage.write_json(self.root / 'plans.json', [{'id': 'new-local-edit'}])
            return {'files': {'plans.json': {'content': '[{"id":"remote"}]'}}}
        with patch.object(sync, '_gist_api', side_effect=fetch):
            self.assertFalse(sync.pull_from_gist())
        self.assertEqual(storage.read_json(self.root / 'plans.json')[0]['id'], 'new-local-edit')
        self.assertIn('changed', sync.get_last_sync_error())

    def test_encrypted_roundtrip_and_truncated_previews_fail_closed(self):
        remote = {'files': {'plans.json.enc': {'content': sync._encrypt('[{"id":"restored"}]', 'test password')}}}
        with patch.object(sync, '_gist_api', return_value=remote):
            self.assertTrue(sync.pull_from_gist())
        self.assertEqual(storage.read_json(self.root / 'plans.json')[0]['id'], 'restored')
        remote['files'] = {'plans.json': {'content': '[', 'truncated': True, 'raw_url': 'https://untrusted.invalid/file'}}
        with patch.object(sync, '_gist_api', return_value=remote):
            self.assertFalse(sync.pull_from_gist())
        self.assertEqual(storage.read_json(self.root / 'plans.json')[0]['id'], 'restored')


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for key, filename in {'DATA_DIR': '', 'SETTINGS_FILE': 'settings.json', 'PLANS_FILE': 'plans.json',
                              'CACHE_FILE': 'videos.json', 'ANALYTICS_FILE': 'analytics.json',
                              'COMPETITORS_FILE': 'competitors.json', 'TITLE_HISTORY_FILE': 'title_history.json'}.items():
            p = patch.object(planner, key, str(self.root / filename))
            p.start(); self.addCleanup(p.stop)
        p = patch.object(planner, '_schedule_sync_push')
        p.start(); self.addCleanup(p.stop)
        for key in ['video_cache', 'plans_cache', 'competitors_cache', 'title_history', 'chat_history']:
            setattr(planner, key, [])
        planner.analytics_cache = {}
        self.client = planner.app.test_client()
        self.headers = {'Origin': 'http://localhost'}
        self.payload = {'chosen_title': 'Example', 'topic': 'Topic',
                        'plan': {'titles': ['Example'], 'outline': [], 'description': 'Sponsor copy\nhttps://example.com',
                                 'vidiq_scores': [{'title': 'Example', 'vidiq_score': 95}], 'vidiq_keywords': ['example']}}

    def post(self, path, payload=None, **kwargs):
        return self.client.post(path, json=payload, headers=self.headers, **kwargs)

    def test_old_launcher_readiness_and_settings_still_work(self):
        self.assertEqual(self.client.get('/planner').status_code, 200)
        self.assertEqual(self.client.get('/settings').status_code, 200)
        self.assertFalse(planner.app.debug)

    def test_concurrent_saves_have_unique_ids_and_preserve_metadata(self):
        def save(_):
            with planner.app.test_client() as client:
                return client.post('/api/plans/save', json=self.payload, headers=self.headers).json['plan_id']
        with ThreadPoolExecutor(max_workers=6) as pool:
            ids = list(pool.map(save, range(12)))
        self.assertEqual(len(set(ids)), 12)
        self.assertEqual(len(storage.read_json(planner.PLANS_FILE)), 12)
        saved = self.client.get('/api/plans/' + ids[0]).json['plan']
        self.assertEqual(saved['full_plan']['vidiq_keywords'], ['example'])
        self.assertEqual(saved['full_plan']['vidiq_scores'][0]['vidiq_score'], 95)
        exported = self.client.get('/api/plans/' + ids[0] + '/export')
        self.assertEqual(exported.status_code, 200)
        self.assertIn(b'Sponsor copy', exported.data)

    def test_legacy_plan_id_remains_editable_and_unknown_id_is_rejected(self):
        planner.plans_cache = [{'id': '20260906123456', 'schema_version': 2}]
        response = self.post('/api/plans/save', dict(self.payload, plan_id='20260906123456'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['plan_id'], '20260906123456')
        self.assertEqual(self.post('/api/plans/save', dict(self.payload, plan_id='missing')).status_code, 404)
        self.assertEqual(len(planner.plans_cache), 1)

    def test_settings_save_preserves_custom_fields(self):
        storage.write_json(planner.SETTINGS_FILE, {'affiliate_links': [{'url': 'https://example.com'}], 'backend_token': 'test'})
        planner.save_settings(channel_name='New name')
        settings = planner.load_settings()
        self.assertEqual(settings['backend_token'], 'test')
        self.assertEqual(settings['affiliate_links'][0]['url'], 'https://example.com')

    def test_cross_site_mutations_and_rebinding_hosts_are_rejected(self):
        self.assertEqual(self.client.post('/api/plans/save', json=self.payload, headers={'Origin': 'https://attacker.invalid'}).status_code, 403)
        self.assertEqual(self.client.post('/api/shutdown').status_code, 403)
        self.assertEqual(self.client.get('/settings', headers={'Host': 'attacker.invalid'}).status_code, 403)
        self.assertEqual(self.client.get('/api/export-data', headers={'Sec-Fetch-Site': 'cross-site'}).status_code, 403)
        self.assertFalse(planner.plans_cache)

    def test_lan_needs_token_but_loopback_does_not(self):
        with patch.dict(os.environ, {'PLANNER_ACCESS_TOKEN': ''}):
            self.assertEqual(self.client.get('/settings', environ_overrides={'REMOTE_ADDR': '192.168.1.2'}).status_code, 403)
            self.assertEqual(self.client.get('/settings').status_code, 200)
        with patch.dict(os.environ, {'PLANNER_ACCESS_TOKEN': 'test-token'}):
            response = self.client.get('/api/plans', headers={'Authorization': 'Bearer test-token'}, environ_overrides={'REMOTE_ADDR': '192.168.1.2'})
            self.assertEqual(response.status_code, 200)

    def test_oauth_state_mismatch_never_exchanges_token(self):
        with self.client.session_transaction() as session:
            session['oauth_state'] = 'expected'
        with patch.object(planner, 'get_oauth_flow') as flow:
            response = self.client.get('/oauth/callback?state=wrong&code=fake')
            self.assertEqual(response.status_code, 400)
            flow.assert_not_called()

    def test_oauth_valid_state_is_passed_once_to_flow(self):
        with self.client.session_transaction() as session:
            session['oauth_state'] = 'expected'
            session['code_verifier'] = 'verifier'
        with patch.object(planner, 'get_oauth_flow') as flow, patch.object(planner, 'save_credentials'):
            response = self.client.get('/oauth/callback?state=expected&code=fake')
            self.assertEqual(response.status_code, 302)
            self.assertEqual(flow.call_args.kwargs['state'], 'expected')
            self.assertEqual(flow.return_value.code_verifier, 'verifier')
        self.assertEqual(self.client.get('/oauth/callback?state=expected&code=fake').status_code, 400)

    def test_invalid_import_leaves_settings_and_cache_untouched(self):
        storage.write_json(planner.SETTINGS_FILE, {'channel_name': 'Local'})
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            z.writestr('settings.json', '{"channel_name":"Remote"}')
            z.writestr('plans.json', '{}')
        buf.seek(0)
        response = self.client.post('/api/import-data', headers=self.headers, data={'file': (buf, 'backup.zip')})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(planner.load_settings()['channel_name'], 'Local')
        self.assertFalse((self.root / 'plans.json').exists())

    def test_startup_never_pulls_remote_backup(self):
        with patch.object(planner, 'pull_from_gist') as pull, patch.object(planner, 'fetch_btc_prices', return_value={}):
            planner._background_startup()
        pull.assert_not_called()

    def test_incomplete_change_is_not_exposed_or_applied(self):
        with patch.object(planner.llm, 'any_configured', return_value=True), \
             patch.object(planner, 'chat_with_claude', return_value='Explanation\n```plan_change\n{"outline": ['), \
             patch('trend_intel.gather_trend_intel', return_value={}):
            response = self.post('/api/chat', {'message': 'Change the outline'})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('```', response.json['error'])
        self.assertFalse(planner.chat_history)
        self.assertFalse(planner.plans_cache)


class AITests(unittest.TestCase):
    def test_anthropic_truncation_is_detected(self):
        with self.assertRaises(llm.IncompleteResponseError):
            llm._anthropic_text(Mock(stop_reason='max_tokens'))

    def test_openai_truncation_is_detected(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"choices":[{"finish_reason":"length","message":{"content":"partial"}}]}'
        with patch.object(llm, 'urlopen', return_value=response):
            with self.assertRaises(llm.IncompleteResponseError):
                llm._openai_compat_chat('http://localhost/v1', '', 'test', '', [], 100)

    def test_invalid_change_shapes_are_rejected(self):
        for change in [{'outline': ['oops']}, {'titles': []}, {'links': [{}]}, {'description': {}}, {'extra': True}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_change(change)
        self.assertEqual(validate_change({'titles': ['Good']}), {'titles': ['Good']})

    def test_existing_launcher_migrates_without_rebuilding(self):
        with tempfile.TemporaryDirectory() as root:
            script = Path(root) / 'BTC Sessions Planner.app/Contents/MacOS/server.sh'
            script.parent.mkdir(parents=True)
            script.write_text('#!/bin/bash\nPROJECT_DIR="/existing/install"\n/usr/sbin/lsof -ti:5000 | xargs kill -9 2>/dev/null\nexec python app.py\n')
            patch_installed_launcher(root)
            first = script.read_text()
            self.assertNotIn('kill -9', first)
            self.assertIn('/existing/install', first)
            self.assertIn('exec python app.py', first)
            patch_installed_launcher(root)
            self.assertEqual(first, script.read_text())


if __name__ == '__main__':
    unittest.main()
