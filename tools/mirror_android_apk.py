"""Копирует проверенную опубликованную APK в публичное хранилище СЭП Чат."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request

REPOSITORY = 'pksep/sep-chat-ui'
APK_NAME = 'SEP-Chat.apk'
UPDATE_NAME = 'android-update.json'
MAX_APK_BYTES = 500 * 1024 * 1024
MAX_JSON_BYTES = 16_384
USER_AGENT = 'SEP-Android-Download-Mirror'


def https_url(value):
    url = urllib.parse.urlsplit(value)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('Нужен постоянный HTTPS-адрес без авторизации и параметров')
    return value.rstrip('/')


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def read_json(url):
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Cache-Control': 'no-cache'})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(MAX_JSON_BYTES + 1)
    if len(data) > MAX_JSON_BYTES:
        raise ValueError('Слишком большой JSON')
    return json.loads(data)


def github(path, method='GET', data=None, raw=None):
    token = os.environ.get('GITHUB_TOKEN', '')
    headers = {'User-Agent': USER_AGENT, 'Accept': 'application/vnd.github+json',
               'X-GitHub-Api-Version': '2022-11-28'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if raw is not None:
        url = 'https://uploads.github.com/repos/' + REPOSITORY + path
        body = raw
        headers['Content-Type'] = 'application/json'
    else:
        url = 'https://api.github.com/repos/' + REPOSITORY + path
        body = None if data is None else json_bytes(data)
        headers['Content-Type'] = 'application/json'
    with urllib.request.urlopen(urllib.request.Request(url, data=body, method=method, headers=headers), timeout=60) as response:
        result = response.read()
        return json.loads(result) if result else None


def validate_release(release, manifest):
    match = re.fullmatch(r'android-(\d+\.\d+\.\d+)-(\d+)', release.get('tag_name', ''))
    if not match or release.get('draft') or release.get('prerelease'):
        raise ValueError('Нужен опубликованный стабильный Android-выпуск')
    if (manifest.get('schemaVersion') != 1 or manifest.get('packageName') != 'sep.pksep.app'
            or manifest.get('versionName') != match[1] or type(manifest.get('versionCode')) is not int
            or manifest['versionCode'] != int(match[2]) or not 0 < manifest['versionCode'] <= 2_147_483_647
            or type(manifest.get('bytes')) is not int or not 0 < manifest['bytes'] <= MAX_APK_BYTES
            or not re.fullmatch(r'[a-f0-9]{64}', manifest.get('sha256', ''))
            or type(manifest.get('minSdk')) is not int or not 26 <= manifest['minSdk'] <= 100):
        raise ValueError('Неверное описание опубликованной APK')
    assets = {asset['name']: asset for asset in release['assets']}
    apk = assets.get(APK_NAME, {})
    expected_url = f"https://github.com/{REPOSITORY}/releases/download/{release['tag_name']}/{APK_NAME}"
    if (apk.get('size') != manifest['bytes'] or apk.get('digest') != 'sha256:' + manifest['sha256']
            or apk.get('state') != 'uploaded' or apk.get('browser_download_url') != expected_url):
        raise ValueError('GitHub не подтвердил размер и SHA-256 исходной APK')
    return apk


def verify_stream(stream, manifest, output=None):
    sha = hashlib.sha256()
    size = 0
    while chunk := stream.read(1024 * 1024):
        size += len(chunk)
        if size > manifest['bytes']:
            raise ValueError('Размер APK превышает заявленный')
        sha.update(chunk)
        if output:
            output.write(chunk)
    if size != manifest['bytes'] or sha.hexdigest() != manifest['sha256']:
        raise ValueError('Размер или SHA-256 APK не совпал')


def assert_not_older(manifest, previous):
    if previous and (previous['versionCode'] > manifest['versionCode'] or
                     previous['versionCode'] == manifest['versionCode'] and previous['sha256'] != manifest['sha256']):
        raise ValueError('Откат версии или замена APK с прежним versionCode запрещены')


def storage_client():
    import boto3
    from botocore.config import Config
    required = ['ANDROID_S3_ENDPOINT', 'ANDROID_S3_BUCKET', 'ANDROID_S3_ACCESS_KEY_ID',
                'ANDROID_S3_SECRET_ACCESS_KEY', 'ANDROID_DOWNLOAD_PUBLIC_BASE_URL']
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise ValueError('Не настроены: ' + ', '.join(missing))
    endpoint = https_url(os.environ['ANDROID_S3_ENDPOINT'])
    base = https_url(os.environ['ANDROID_DOWNLOAD_PUBLIC_BASE_URL'])
    bucket = os.environ['ANDROID_S3_BUCKET']
    if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', bucket):
        raise ValueError('Неверное имя bucket')
    client = boto3.client('s3', endpoint_url=endpoint,
        aws_access_key_id=os.environ['ANDROID_S3_ACCESS_KEY_ID'],
        aws_secret_access_key=os.environ['ANDROID_S3_SECRET_ACCESS_KEY'],
        config=Config(region_name='us-east-1', s3={'addressing_style': 'path'},
                      request_checksum_calculation='when_required', response_checksum_validation='when_required',
                      retries={'mode': 'standard', 'max_attempts': 3}, connect_timeout=10, read_timeout=60))
    return client, bucket, base


def object_exists(client, bucket, key):
    # Явный prefix работает с ListBucket, ограниченным только android/*.
    result = client.list_objects_v2(Bucket=bucket, Prefix=key, MaxKeys=1)
    return any(item['Key'] == key for item in result.get('Contents', []))


def stored_json(client, bucket, key):
    if not object_exists(client, bucket, key):
        return None
    result = client.get_object(Bucket=bucket, Key=key)
    with result['Body'] as body:
        data = body.read(MAX_JSON_BYTES + 1)
    if len(data) > MAX_JSON_BYTES:
        raise ValueError('Слишком большой JSON в хранилище')
    return json.loads(data)


def public_head(url, size, sha256):
    with urllib.request.urlopen(urllib.request.Request(url, method='HEAD', headers={'User-Agent': USER_AGENT,
                                     'Cache-Control': 'no-cache'}), timeout=60) as response:
        if response.status != 200 or int(response.headers.get('Content-Length', -1)) != size:
            raise ValueError('Публичная APK недоступна или имеет неверный размер')
        if response.headers.get('x-amz-meta-sha256') != sha256:
            raise ValueError('Метаданные публичной APK не совпали')
        if urllib.parse.urlsplit(response.geturl()).hostname != urllib.parse.urlsplit(url).hostname:
            raise ValueError('Публичный адрес перенаправляет скачивание на другой сервер')


def publish_storage(client, bucket, base, release, manifest, apk_path, check_latest):
    previous = stored_json(client, bucket, 'android/latest.json')
    assert_not_older(manifest, previous)
    version_key = f"android/releases/{release['tag_name']}/{APK_NAME}"
    version_url = f'{base}/{version_key}'
    # Бинарники по версии неизменяемы; приложение проверяет их SHA-256 перед установкой.
    existing = client.head_object(Bucket=bucket, Key=version_key) if object_exists(client, bucket, version_key) else None
    metadata = {'sha256': manifest['sha256'], 'version-code': str(manifest['versionCode'])}
    if existing:
        if existing['ContentLength'] != manifest['bytes'] or existing.get('Metadata', {}).get('sha256') != manifest['sha256']:
            raise ValueError('Файл этой версии уже существует с другим содержимым')
    else:
        with apk_path.open('rb') as body:
            md5 = base64.b64encode(hashlib.file_digest(body, 'md5').digest()).decode('ascii')
            body.seek(0)
            client.put_object(Bucket=bucket, Key=version_key, Body=body, ContentLength=manifest['bytes'],
                ContentMD5=md5, ContentType='application/vnd.android.package-archive', Metadata=metadata,
                ContentDisposition='attachment; filename="SEP-Chat.apk"', CacheControl='public, max-age=31536000, immutable')
    public_head(version_url, manifest['bytes'], manifest['sha256'])
    with urllib.request.urlopen(urllib.request.Request(version_url, headers={'User-Agent': USER_AGENT}), timeout=60) as response:
        verify_stream(response, manifest)
    check_latest()
    # В хранилище используются атомарные Put/Copy; незавершённый файл не станет latest.
    client.copy_object(Bucket=bucket, Key='android/SEP-Chat.apk', CopySource={'Bucket': bucket, 'Key': version_key},
        MetadataDirective='REPLACE', Metadata=metadata, ContentType='application/vnd.android.package-archive',
        ContentDisposition='attachment; filename="SEP-Chat.apk"', CacheControl='no-cache, max-age=0, must-revalidate')
    apk_url = f'{base}/android/{APK_NAME}'
    public_head(apk_url, manifest['bytes'], manifest['sha256'])
    update = {**manifest, 'apkUrl': version_url}
    client.put_object(Bucket=bucket, Key='android/latest.json', Body=json_bytes(update), ContentType='application/json',
                      CacheControl='no-cache, max-age=0, must-revalidate')
    if read_json(f'{base}/android/latest.json') != update:
        raise ValueError('Публичное описание обновления не совпало')
    return {'apkUrl': apk_url, 'update': update, 'manifestUrl': f'{base}/android/latest.json'}


def current_storage(client, bucket, base, release, manifest):
    previous = stored_json(client, bucket, 'android/latest.json')
    assert_not_older(manifest, previous)
    version_url = f"{base}/android/releases/{release['tag_name']}/{APK_NAME}"
    if previous != {**manifest, 'apkUrl': version_url}:
        return None
    apk_url = f'{base}/android/{APK_NAME}'
    public_head(version_url, manifest['bytes'], manifest['sha256'])
    public_head(apk_url, manifest['bytes'], manifest['sha256'])
    return {'apkUrl': apk_url, 'update': previous, 'manifestUrl': f'{base}/android/latest.json'}


def switch_github_downloads(release, original_update, result):
    update = result['update']
    assets = {a['name']: a for a in release['assets']}
    old = assets[UPDATE_NAME]
    desired = json_bytes(update)
    if read_json(old['browser_download_url']) != update:
        temporary_name = 'android-update-server.json'
        stale = assets.get(temporary_name)
        if stale:
            github('/releases/assets/' + str(stale['id']), 'DELETE')
        uploaded = github(f"/releases/{release['id']}/assets?name={temporary_name}", 'POST', raw=desired)
        if uploaded.get('digest') != 'sha256:' + hashlib.sha256(desired).hexdigest():
            raise ValueError('GitHub не подтвердил SHA-256 нового описания обновления')
        github('/releases/assets/' + str(old['id']), 'DELETE')
        try:
            github('/releases/assets/' + str(uploaded['id']), 'PATCH', data={'name': UPDATE_NAME})
        except Exception:
            # Если переключение не удалось, возвращаем маленький исходный JSON.
            github(f"/releases/{release['id']}/assets?name={UPDATE_NAME}", 'POST', raw=json_bytes(original_update))
            raise
    current = github('/contents/android-download.json?ref=main')
    config = json.loads(base64.b64decode(current['content']))
    if config.get('apkUrl') != result['apkUrl']:
        config['apkUrl'] = result['apkUrl']
        config['revision'] = f"android-server-{update['versionCode']}-{update['sha256'][:12]}"
        github('/contents/android-download.json', 'PUT', data={
            'message': 'chore: serve verified Android APK from SEP storage [skip ci]', 'branch': 'main',
            'sha': current['sha'], 'content': base64.b64encode(json_bytes(config)).decode('ascii')})
    # Commit от GITHUB_TOKEN сам не запускает branch-based GitHub Pages.
    live = read_json('https://pksep.github.io/sep-chat-ui/android-download.json')
    if live.get('apkUrl') != result['apkUrl']:
        github('/pages/builds', 'POST', data={})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-tag', default='')
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--result', type=Path)
    args = parser.parse_args()
    release = github('/releases/latest')
    if args.release_tag and args.release_tag != release['tag_name']:
        raise ValueError('Можно зеркалировать только последний опубликованный выпуск')
    update_asset = next(a for a in release['assets'] if a['name'] == UPDATE_NAME)
    manifest = read_json(update_asset['browser_download_url'])
    apk = validate_release(release, manifest)
    if args.check_only:
        print(json.dumps({'tag': release['tag_name'], 'sha256': manifest['sha256'], 'bytes': manifest['bytes'], 'validated': True}))
        return
    client, bucket, base = storage_client()

    def check_latest():
        latest = github('/releases/latest')
        if latest['id'] != release['id'] or latest['tag_name'] != release['tag_name']:
            raise ValueError('Уже опубликована новая версия; предыдущая не станет latest')

    result = current_storage(client, bucket, base, release, manifest)
    with tempfile.TemporaryDirectory(prefix='sep-android-mirror-') as directory:
        if result is None:
            apk_path = Path(directory) / APK_NAME
            with urllib.request.urlopen(apk['browser_download_url'], timeout=60) as response, apk_path.open('wb') as output:
                verify_stream(response, manifest, output)
            print('Исходная APK скачана, размер и SHA-256 проверены', flush=True)
            result = publish_storage(client, bucket, base, release, manifest, apk_path, check_latest)
        check_latest()
        switch_github_downloads(release, manifest, result)
        result.update({'tag': release['tag_name'], 'sha256': manifest['sha256'], 'bytes': manifest['bytes']})
        if args.result:
            args.result.write_bytes(json_bytes(result))
        print(json.dumps({'published': True, 'apkUrl': result['apkUrl'], 'version': manifest['versionName'],
                          'versionCode': manifest['versionCode'], 'sha256': manifest['sha256']}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Не выводим сырые ответы SDK: в них могут быть детали подписанного запроса.
        if isinstance(error, ValueError):
            print(str(error))
        else:
            code = getattr(error, 'response', {}).get('Error', {}).get('Code')
            print('Публикация APK не завершена: ' + (str(code) if code else type(error).__name__))
        raise SystemExit(1)
