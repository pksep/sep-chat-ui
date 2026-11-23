# Раздача Android APK с серверов СЭП

## Текущая публикация: GitLab

23 ноября DevOps настроил хранилище и перенёс секреты публикации в GitLab,
проект [github-mirror/sep-chat-ui](https://gitlab-dev.pksep.ru/github-mirror/sep-chat-ui).
Запуск пока ручной: ветка `main`,
переменная `PUBLISH_ANDROID=true`. Переданный DevOps постоянный адрес:
`https://minio-cloud.pksep.ru/sep-chat-downloads/android/SEP-Chat.apk`.
GitHub остаётся источником готовой APK и хостингом Pages.

Первая успешная публикация:
[pipeline №7676](https://gitlab-dev.pksep.ru/github-mirror/sep-chat-ui/-/pipelines/7676).
Публичная APK 2.0.0/20002 скачана целиком (144 698 665 байт), SHA-256 совпал
с исходным `ff25047517a56228694897a89bed89e16f2aac6b1c2d09f0a85092bdd65e7549`.
Проверена докачка: Range отвечает 206 и возвращает запрошенный диапазон.

Workflow GitHub **Mirror Android APK отключён**. Не добавлять в GitHub ключи S3
и не включать его автоматически. Инструкция настройки GitHub ниже сохранена
как описание исходного процесса; активная публикация выполняется в GitLab.
После проверки публичной копии QR-конфигурация и GitHub native-feed переключаются
на серверные адреса без пересборки клиентов.

## Исходный workflow GitHub — отключён

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
- `s3:ListBucket`, ограниченный условием `s3:prefix = android/*`, чтобы отличать
  отсутствующий объект от запрета доступа; скрипт всегда передаёт этот prefix;
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
