# Раздача Android APK с серверов СЭП

Готовая проверенная APK остаётся в GitHub Releases как исходный артефакт.
Workflow `Mirror Android APK` копирует её в отдельный публичный MinIO bucket.
Ручной запуск: Actions → Mirror Android APK → Run workflow. После настройки
доступа новые публикации Android запускают его автоматически через `release.published`.
Если выпуск создаёт другой workflow через `GITHUB_TOKEN`, ему нужно явно вызвать
`workflow_dispatch`, поскольку такой release event не запускает следующий workflow.

## Настройка DevOps

Отдельный bucket `sep-chat-downloads` на `https://minio-cloud.pksep.ru`:

- анонимный `s3:GetObject` только для `sep-chat-downloads/android/*`;
- пользователь публикации: `s3:GetObject` и `s3:PutObject` только для `android/*`;
- для ответа 404 на отсутствующий объект может понадобиться `s3:ListBucket`,
  ограниченный префиксом `android/`;
- существующие bucket с переписками и их политики не менять.

В репозитории **pksep/sep-chat-ui** добавить GitHub Actions Secrets:

- `ANDROID_S3_ACCESS_KEY_ID`;
- `ANDROID_S3_SECRET_ACCESS_KEY`.

И Actions Variables:

- `ANDROID_S3_ENDPOINT=https://minio-cloud.pksep.ru`;
- `ANDROID_S3_BUCKET=sep-chat-downloads`;
- `ANDROID_DOWNLOAD_PUBLIC_BASE_URL=https://minio-cloud.pksep.ru/sep-chat-downloads`.

Ключи не добавлять в Git и не присылать в чат. Скрипт не создаёт bucket,
не меняет его политику, не получает доступ к другим каталогам и не пересобирает APK.

## Порядок публикации

1. Сверить последний стабильный Android-релиз, package name, versionCode,
   размер и SHA-256 из описания и метаданных GitHub.
2. Скачать исходную APK и сверить SHA-256 целиком.
3. Загрузить `android/releases/android-VERSION-CODE/SEP-Chat.apk` с проверкой MD5
   на стороне MinIO. Адрес версии неизменяемый.
4. Скачать публичный файл и проверить SHA-256 целиком.
5. Повторно проверить, что в GitHub не появился более новый выпуск.
6. Атомарно скопировать готовый файл в `android/SEP-Chat.apk`; записать `android/latest.json`.
7. Переключить APK URL в маленьком `android-update.json` GitHub Release: уже
   установленные Android продолжают использовать прежний feed, но файл скачивают
   с серверов СЭП. SHA-256 и подпись APK остаются прежними.
8. Переключить `apkUrl` в `android-download.json` и явно запустить GitHub Pages.
   Существующий QR остаётся рабочим: его страница читает этот JSON.

При сбое проверки исходного/публичного файла ссылки GitHub и QR не меняются.
Откат versionCode и замена содержимого ранее опубликованной версии запрещены.
Повторный запуск с той же APK не перекачивает большой файл.

После настройки и успешного запуска постоянный адрес APK:
`https://minio-cloud.pksep.ru/sep-chat-downloads/android/SEP-Chat.apk`.
Это адрес назначения; считать его доступным можно только после публичной проверки.

Локальные проверки:

```text
python -m unittest discover -s tools -p "test_*.py" -v
python tools/mirror_android_apk.py --check-only
```
