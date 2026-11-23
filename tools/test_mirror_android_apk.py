import base64
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import mirror_android_apk as mirror


class MissingObject(Exception):
    response = {'Error': {'Code': 'NoSuchKey'}}


class FakeStorage:
    def __init__(self):
        self.files = {}
        self.writes = []

    def get_object(self, Bucket, Key):
        if Key not in self.files:
            raise MissingObject()
        return {'Body': io.BytesIO(self.files[Key]['body'])}

    def list_objects_v2(self, Bucket, Prefix, MaxKeys):
        return {'Contents': [{'Key': key} for key in sorted(self.files) if key.startswith(Prefix)][:MaxKeys]}

    def head_object(self, Bucket, Key):
        if Key not in self.files:
            raise MissingObject()
        value = self.files[Key]
        return {'ContentLength': len(value['body']), 'Metadata': value.get('Metadata', {})}

    def put_object(self, **kwargs):
        value = kwargs['Body']
        data = value.read() if hasattr(value, 'read') else value
        self.files[kwargs['Key']] = {**kwargs, 'body': data}
        self.writes.append(kwargs['Key'])
        if 'ContentMD5' in kwargs:
            assert kwargs['ContentMD5'] == base64.b64encode(hashlib.md5(data).digest()).decode()

    def copy_object(self, **kwargs):
        data = self.files[kwargs['CopySource']['Key']]['body']
        self.files[kwargs['Key']] = {**kwargs, 'body': data}
        self.writes.append(kwargs['Key'])


class AndroidMirrorTest(unittest.TestCase):
    def setUp(self):
        self.apk = b'PK\x03\x04 synthetic signed APK bytes'
        self.manifest = {'schemaVersion': 1, 'packageName': 'sep.pksep.app', 'versionName': '2.0.0',
            'versionCode': 20002, 'bytes': len(self.apk), 'sha256': hashlib.sha256(self.apk).hexdigest(),
            'minSdk': 26, 'apkUrl': 'https://github.com/pksep/sep-chat-ui/releases/download/android-2.0.0-20002/SEP-Chat.apk'}
        self.release = {'id': 1, 'tag_name': 'android-2.0.0-20002', 'draft': False, 'prerelease': False,
            'assets': [{'name': mirror.APK_NAME, 'size': len(self.apk), 'digest': 'sha256:' + self.manifest['sha256'],
                'state': 'uploaded', 'browser_download_url': self.manifest['apkUrl']},
                {'id': 2, 'name': mirror.UPDATE_NAME, 'browser_download_url': 'https://github.com/test/update.json'}]}
        self.storage = FakeStorage()
        self.directory = tempfile.TemporaryDirectory(prefix='sep-mirror-test-')
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'test.apk'
        self.path.write_bytes(self.apk)
        self.base = 'https://downloads.example.test/sep-chat-downloads'

    def publish(self, check_latest=lambda: None):
        with patch.object(mirror, 'public_head'), patch.object(mirror.urllib.request, 'urlopen',
                side_effect=lambda *args, **kwargs: io.BytesIO(self.apk)), patch.object(mirror, 'read_json',
                side_effect=lambda url: json.loads(self.storage.files['android/latest.json']['body'])):
            return mirror.publish_storage(self.storage, 'downloads', self.base, self.release,
                                          self.manifest, self.path, check_latest)

    def test_only_verified_stable_source_can_be_mirrored(self):
        self.assertEqual(mirror.validate_release(self.release, self.manifest)['size'], len(self.apk))
        for changes in [{'packageName': 'another.app'}, {'versionCode': True}, {'versionCode': 20001},
                        {'sha256': '0' * 64}, {'bytes': -1}, {'minSdk': 25}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                mirror.validate_release(self.release, {**self.manifest, **changes})
        for changes in [{'draft': True}, {'prerelease': True}, {'tag_name': 'v2.0.0'}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                mirror.validate_release({**self.release, **changes}, self.manifest)

    def test_rejects_corrupt_partial_or_oversized_apk(self):
        for apk in [self.apk[:-1], self.apk + b'x', b'x' + self.apk[1:]]:
            with self.subTest(apk=apk), self.assertRaises(ValueError):
                mirror.verify_stream(io.BytesIO(apk), self.manifest)

    def test_versioned_public_file_is_verified_before_latest(self):
        result = self.publish()
        self.assertEqual(self.storage.writes, ['android/releases/android-2.0.0-20002/SEP-Chat.apk',
                                               'android/SEP-Chat.apk', 'android/latest.json'])
        self.assertEqual(self.storage.files['android/SEP-Chat.apk']['body'], self.apk)
        self.assertEqual(result['update']['sha256'], self.manifest['sha256'])
        self.assertEqual(result['update']['apkUrl'], self.base + '/android/releases/android-2.0.0-20002/SEP-Chat.apk')
        self.assertEqual(result['apkUrl'], self.base + '/android/SEP-Chat.apk')

    def test_failed_public_hash_does_not_switch_latest(self):
        with patch.object(mirror, 'public_head'), patch.object(mirror.urllib.request, 'urlopen',
                return_value=io.BytesIO(b'corrupt')), self.assertRaises(ValueError):
            mirror.publish_storage(self.storage, 'downloads', self.base, self.release, self.manifest, self.path, lambda: None)
        self.assertNotIn('android/SEP-Chat.apk', self.storage.files)
        self.assertNotIn('android/latest.json', self.storage.files)

    def test_newer_version_is_not_replaced(self):
        previous = {**self.manifest, 'versionCode': 20003}
        self.storage.files['android/latest.json'] = {'body': mirror.json_bytes(previous)}
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(self.storage.writes, [])

    def test_existing_version_is_immutable(self):
        self.storage.files['android/releases/android-2.0.0-20002/SEP-Chat.apk'] = {
            'body': self.apk, 'Metadata': {'sha256': '0' * 64}}
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(self.storage.writes, [])

    def test_new_github_release_during_upload_prevents_switch(self):
        def changed():
            raise ValueError('new release')
        with self.assertRaises(ValueError):
            self.publish(changed)
        self.assertNotIn('android/SEP-Chat.apk', self.storage.files)

    def test_repeated_mirror_uses_current_files(self):
        self.publish()
        self.storage.writes.clear()
        with patch.object(mirror, 'public_head'):
            result = mirror.current_storage(self.storage, 'downloads', self.base, self.release, self.manifest)
        self.assertEqual(result['apkUrl'], self.base + '/android/SEP-Chat.apk')
        self.assertEqual(self.storage.writes, [])

    def test_switch_keeps_qr_and_updates_native_download_url(self):
        result = self.publish()
        calls = []
        original = {'enabled': True, 'qrImageUrl': 'https://example.test/qr.png', 'apkUrl': 'https://github.com/old.apk'}

        def api(path, method='GET', data=None, raw=None):
            calls.append((path, method, data, raw))
            if method == 'POST' and raw:
                return {'id': 3, 'digest': 'sha256:' + hashlib.sha256(raw).hexdigest()}
            if path.startswith('/contents/') and method == 'GET':
                return {'sha': 'old', 'content': base64.b64encode(mirror.json_bytes(original)).decode()}

        with patch.object(mirror, 'github', side_effect=api), patch.object(mirror, 'read_json', return_value=self.manifest):
            mirror.switch_github_downloads(self.release, self.manifest, result)
        config_write = next(call[2] for call in calls if call[1] == 'PUT')
        config = json.loads(base64.b64decode(config_write['content']))
        self.assertEqual(config['qrImageUrl'], original['qrImageUrl'])
        self.assertEqual(config['apkUrl'], result['apkUrl'])
        self.assertTrue(config['enabled'])
        self.assertTrue(any(call[0] == '/pages/builds' and call[1] == 'POST' for call in calls))
        uploaded = next(call[3] for call in calls if call[1] == 'POST' and call[3])
        self.assertEqual(json.loads(uploaded)['apkUrl'], result['update']['apkUrl'])

    def test_failed_native_feed_switch_restores_original_before_qr_change(self):
        result = self.publish()
        calls = []

        def api(path, method='GET', data=None, raw=None):
            calls.append((path, method, data, raw))
            if method == 'POST' and raw:
                return {'id': 3, 'digest': 'sha256:' + hashlib.sha256(raw).hexdigest()}
            if method == 'PATCH':
                raise RuntimeError('rename failed')

        with patch.object(mirror, 'github', side_effect=api), patch.object(mirror, 'read_json', return_value=self.manifest), self.assertRaises(RuntimeError):
            mirror.switch_github_downloads(self.release, self.manifest, result)
        self.assertFalse(any(call[1] == 'PUT' for call in calls))
        restored = [call[3] for call in calls if call[1] == 'POST' and call[3]][-1]
        self.assertEqual(json.loads(restored), self.manifest)


if __name__ == '__main__':
    unittest.main()
