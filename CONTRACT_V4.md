# Ketoy v4 — компоненты и разрешённые сценарии

17 ноября 2026. Пользователь разрешил расширение APK: 2.0.0/20001.
Статус приёмки — WORKING_SLICE.md и PROJECT_PLAN.md.

## Что редактируется

203 адаптера всех разделов сохраняют существующие данные Kotlin, Room, outbox
и callbacks. Любой адаптер принимает общий style, новые соседние JSON-блоки
или явную замену `mode: "replace"`. В decorate обязателен один Native,
в replace Native отсутствует. Без настройки вызывается исходная реализация.
Реестр: `doc/ketoy/catalog.json`.

Новые блоки: column, row, box, card, text, button, textfield, switch, checkbox,
select, repeat, view, icon, image, progress, divider, spacer. Иконки — нативные
векторы. Image принимает PNG/JPEG/WebP из публичного `sep-chat-ui/assets`:
отдельный HTTP без cookies, авторизации и redirect. Файлы устройства,
произвольные хосты, SVG/HTML и параметры URL не поддерживаются.

runtime содержит permissions, state, actions, views, pages, dialogs.
Состояние форм принадлежит текущему входу на поверхность. Новая сессия/топик
создают новый runtime и отменяют его работу. Фоновое обновление JSON
не пересоздаёт активный ввод; новый документ применяется при следующем входе.

## Данные и действия

`{"bind":"state.name"}` в аргументе/присваивании читает текущее состояние.
У Text props.bind — строка state.name, app.displayName, local.title или item.title.
local ограничен контрактом адаптера. Repeat читает список по bind; внутри
доступен item. View переиспользует runtime.views. Scene в body адаптера
подключает именованное дерево. visibleBind/enabledBind принимают boolean.

Команды: set, invoke, local, sequence, if, page, dialog, back, dismiss.
Invoke имеет capability, args и необязательный resultKey; результат читается
через state.result.ok. Sequence вычисляет следующий шаг после предыдущего.
If выбирает then/else по boolean condition. Повторное нажатие занятой команды
отключено, отмена не становится ошибкой; сетевые мутации автоматически не повторяются.

| Capability | Аргументы | Реализация |
|---|---|---|
| navigate.section | section: chats/calls/ai/clients/profile | Штатная навигация и допуск разделов |
| navigate.topic | topicId | Штатное открытие переписки |
| navigate.folders / navigate.newChat / navigate.back | нет | Штатные маршруты |
| profile.qr | нет | Нативное окно выдачи QR профиля |
| profile.status | icon, text | DirectoryRepository |
| updates.check / updates.install | нет | Общий NativeAppUpdate |
| api.topics.refresh / api.profile.refresh | нет | ChatRepository / DirectoryRepository |
| topic.pin / topic.mute / topic.markUnread | topicId, value:boolean | TopicActionsRepository и optimistic-проекция |

Имена объявляются в permissions. Этот список не повышает права аккаунта:
проверяются текущая сессия и нативная доступность. Работа репозитория отменяется
при смене входа. SessionCommandScope переносит ожидаемое поколение до самого
захвата сессии в ChatRepository, DirectoryRepository и TopicActionsEngine:
повторный вход тем же аккаунтом не принимает старую команду. Страницы/диалоги
используют app/state и именованные возможности;
callbacks исходного адаптера в них не переносятся. Inline local-кнопки получают
актуальные callbacks/disabled при recomposition.

app публикует displayName, version, connection, clientsAllowed, aiAllowed и первые
100 UI-сводок topics (id/title/preview/unreadCount/muted/pinned). Привязки локальные,
данные не отправляются в публичный репозиторий. Токены, cookies, произвольный HTTP,
Kotlin/JS, WebView и reflection JSON не получает.

## Стили и пределы

style: padding и paddingStart/End/Top/Bottom, width/height/minHeight/maxHeight,
fillWidth, corner, background, borderColor/borderWidth, alpha, visible.
Цвет — #RRGGBB либо опубликованная роль @theme. Размеры — dp, текст — sp.
Прокручиваемый column требует height/maxHeight, исключая бесконечные ограничения
вложенных scroll. Типизированный config и темы v3 сохраняются.

Пределы: JSON 256 KiB, 64 поля состояния, 128 действий, 1024 исходных узла,
2048 единиц развёрнутого дерева, 64 шага цепочки, глубина шаблонов 10,
история страниц 16. Repeat учитывается после разворачивания. Циклы отклоняются
до применения. Image имеет вес 128, тело <=2 MiB, <=4 млн пикселей, сторона <=4096.
Невалидный документ не заменяет последний валидный кеш.

## Публикация

20000 читает manifest.json (v3); 20001 — manifest-v4.json. Старый endpoint
не заменять схемой v4. В v4 обязателен minVersionCode >=20001.
Пример форм/QR: `doc/ketoy/public/example-v4-profile.json`. Для отката содержимое
rollback-v4.json публикуется как manifest-v4.json с новой revision.
Наличие отдельного rollback-файла ничего не переключает. Проверка обновлений —
не чаще 30 минут активной работы; применение при следующем входе на поверхность.

Перед публикацией:

```text
node tools/ketoy-component-inventory.mjs --verify
gradlew.bat :app:testDebugUnitTest --tests ru.pksep.chat.remoteui.RemoteUiPublishedManifestTest -PketoyManifestFile=<путь к JSON>
```

Весь проект доступен для оформления/компоновки через адаптеры. Формы, экраны
и сочетания опубликованных действий можно добавлять JSON без следующей APK.
Новый нативный тип, алгоритм, разрешение Android или бизнес-API требуют обновления
приложения: эта версия не загружает исполняемый код.

## QR профиля

POST существующего /v2/auth/qr через общую сессию/refresh, с запасом жизни доступа
100 секунд. Ответ: code из 22 символов и expires_at; ссылка того же API /v2/q#qr=….
После подтверждённого 401 — штатный один повтор. Код обновляется за 10 секунд
до истечения; закрытие/смена входа отменяют работу. Поздний ответ не открывает окно.
На resume истёкший код заменяется. Код не пишется в Room, JSON или отчёты.
Проверки только синтетические, без реального QR-входа.
