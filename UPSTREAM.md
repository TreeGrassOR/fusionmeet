# Отличия от upstream и обновление

Форк основан на [suitenumerique/meet](https://github.com/suitenumerique/meet), коммит
`dce67e3` («add Menshen token-exchange authentication to external API»).

Почти всё добавлено новыми файлами. Изменённые файлы upstream перечислены ниже.
При слиянии конфликты возможны только в них.

## Новые файлы и каталоги

| Путь | Что это |
|---|---|
| `asr/` | сервис распознавания речи GigaAM Multilingual (FastAPI, GPU) |
| `src/agents/gigaam_stt.py`, `transcript_store.py`, `transcription_control.py`, `transcription_reconciler.py`, `transcription_settings.py`, `tests/` | STT-провайдер `gigaam`, хранение расшифровки в S3, управление через метаданные комнаты, повторный запуск агента после падения, модель распознавания комнаты |
| `src/backend/transcripts/` | Django-приложение: старт и остановка расшифровки, список, скачивание, хук агента |
| `src/backend/calls/` | Django-приложение «Звонки»: история звонков по вебхукам LiveKit (свой URL `calls/webhooks-livekit/`, второй в `webhook.urls`), сохранение сообщений чата от клиентов, подключённых к звонку (проверка через LiveKit RoomService, `calls/room_service.py`), закрытие звонков, чей `room_finished` потерян, список и карточка звонка, расшифровка звонка для участников, название звонков комнаты |
| `src/mail/mjml/transcript_ready.mjml` | письмо инициатору о готовой расшифровке |
| `src/frontend/src/features/transcription/` | кнопки «Расшифровка» и «Запись», индикатор, страница «Мои записи и расшифровки», выбор модели распознавания |
| `src/frontend/src/primitives/Slider.tsx`, `src/frontend/src/stores/participantAudio.ts`, компоненты громкости в `features/participants`, `features/participantTile`, `features/rooms` | громкость участников и, отдельно, звука их трансляции |
| `src/frontend/src/features/rooms/livekit/utils/screenShare.ts`, `startScreenShare.ts`, `hooks/useScreenShareToggle.ts`, `src/frontend/src/features/notifications/components/ToastScreenShareAudioNotShared.tsx` | демонстрация экрана: захват без звука самой встречи (`restrictOwnAudio`, иначе звук не передаётся и показывается уведомление), публикация в VP8 с simulcast и стереозвуком |
| `src/frontend/src/features/rooms/livekit/components/ConnectionStateToast.tsx` | переводимая замена уведомления о подключении из `@livekit/components-react` (там тексты зашиты по-английски) |
| `src/frontend/src/features/calls/` | главная вошедшего пользователя в духе Яндекс Телемоста (при `calls.enabled`): панель разделов, список звонков с поиском, карточки «Новый звонок», «Подключиться к звонку», «Запланировать в Календаре» (.ics и ссылка Google Календаря), ссылка-приглашение; звонок `/calls/<id>` с вкладками «Чат», «Расшифровка», «Записи», «Участники»; `ChatPersistence` — отправка своих сообщений чата в историю звонка |
| `src/frontend/src/locales/*/transcription.json`, `*/participantAudio.json`, `*/screenShare.json`, `{en,ru,fr}/calls.json` | строки новых функций (отдельные пространства имён, чтобы не трогать файлы upstream) |
| `src/frontend/src/stores/theme.ts`, `stores/theme.test.ts`, `hooks/useApplyTheme.ts`, `features/settings/components/AppearanceFields.tsx`, `locales/{en,ru,fr}/appearance.json` | тёмная тема: выбор «Как в системе / Светлая / Тёмная» (хранится в браузере, ключ `app_theme`), `data-theme` на `<html>` и в окне «Картинка в картинке» |
| `src/frontend/src/locales/ru/`, `src/backend/locale/ru_RU/`, `src/summary/summary/core/locales/ru.py` | русская локализация |
| `bin/check-i18n-ru` | проверка, что ключи `ru` совпадают с `en` |
| `docs/features/gigaam-transcription.md` | описание расшифровки по участникам |
| `src/frontend/src/components/BrandLogo.tsx`, `src/frontend/public/assets/fusion-meet-email.png` | логотип Fusion Meet: знак (inline SVG, `currentColor`) и название из `VITE_APP_TITLE`; широкий логотип для писем |
| `src/frontend/src/features/rooms/utils/joinMediaDefaults.ts`, `applyJoinMediaDefaults.ts`, `src/frontend/src/features/settings/components/JoinMediaDefaultsFields.tsx`, `src/frontend/src/locales/{en,ru,fr}/joinDefaults.json`, `src/frontend/src/stores/userPreferences.test.ts` | «Входить с выключенным микрофоном / камерой» (настройка браузера) |
| `deploy/` (кроме `deploy/paas/`) | docker compose для одного сервера с GPU, скрипты установки |
| `UPSTREAM.md` | этот файл |

## Изменённые файлы upstream

| Файл | Изменение |
|---|---|
| `src/agents/multi_user_transcriber.py` | ветка `STT_PROVIDER=gigaam`, приём финальных фраз, управление расшифровкой по метаданным комнаты, модель распознавания комнаты, устойчивое закрытие сессий |
| `src/agents/pyproject.toml`, `src/agents/uv.lock` | `tzdata`, `pytest`, `pytest-asyncio` в dev, настройки pytest |
| `src/backend/meet/settings.py` | приложения `transcripts` и `calls`, настройки `TRANSCRIPTION_*` и `CALLS_*`; `ru-ru` первым в `LANGUAGES`, `LANGUAGE_CODE = "ru-ru"` (в конфигурации `Test` оставлен `en-us`, потому что тесты upstream проверяют английские тексты) |
| `src/backend/meet/urls.py` | подключение `transcripts.urls` и `calls.urls` |
| `src/backend/core/api/__init__.py` | `transcription.enabled` и `calls.enabled` в `/api/v1.0/config/` |
| `src/frontend/src/api/useConfig.ts` | типы `transcription` и `calls` в конфигурации; удалены `use_french_gov_footer` и `use_proconnect_button` |
| `src/frontend/src/routes.ts` | маршруты `/my-recordings` и `/calls/<id>` (звонок из истории); удалены юридические страницы (`/mentions-legales`, `/accessibilite`, `/conditions-utilisation`) |
| `src/frontend/src/layout/Header.tsx` | пункт «Мои записи и расшифровки» в меню пользователя; `BrandLogo` вместо `/assets/logo.svg`; без проверок удалённых юридических страниц |
| `src/frontend/src/features/rooms/livekit/prefabs/ControlBar/DesktopControlBar.tsx`, `MobileControlBar.tsx` | кнопки «Расшифровка» и «Запись» |
| `src/frontend/src/features/settings/components/SettingsDialogExtended.tsx`, `tabs/TranscriptionTab.tsx` | вкладка «Расшифровка» в настройках: выбор модели распознавания для тех, кто может запускать расшифровку; язык встречи (WhisperX) показан, только если доступен режим записи `transcript` |
| `src/frontend/src/features/rooms/livekit/prefabs/VideoConference.tsx` | провайдер расшифровки, синхронизатор громкости, переводимый `ConnectionStateToast`, `ChatPersistence` рядом с `ChatProvider` |
| `src/frontend/src/features/pip/components/PipView.tsx` | переводимый `ConnectionStateToast` |
| `src/frontend/src/features/rooms/livekit/components/controls/StartMediaButton.tsx` | перевод «Start Audio» и «Start Video» |
| `src/frontend/src/locales/{en,fr,de,es,nl}/rooms.json` | ключи `controls.startMedia.*` и `connectionState.*` для двух строк выше; удалены `error.screenShare.helpLinkText` и `helpLinkLabel`; `chat.disclaimer`: сообщения сохраняются в истории звонка |
| `src/frontend/src/features/rooms/components/Conference.tsx` | `webAudioMix` там, где браузер поддерживает усиление выше 100 %; `userConfig` — снимок `userChoicesStore` при монтировании вместо `usePersistentUserChoices` (иначе при «Создать встречу» читалось старое состояние из localStorage) |
| `src/frontend/src/features/participants/components/menu/ParticipantMenu.tsx`, `ParticipantMenuButton.tsx` | ползунок громкости и «Выключить звук для меня» |
| `src/frontend/src/features/participantTile/components/ParticipantMetadata.tsx`, `participantTileFocus/ParticipantTileFocus.tsx` | кнопка громкости на плитке и значок «звук выключен для вас»; на плитке трансляции со звуком — громкость трансляции и значок «звук трансляции выключен для вас» |
| `src/frontend/src/features/rooms/livekit/components/controls/ScreenShareToggle.tsx`, `src/frontend/src/features/pip/components/controls/PipOptionsMenuItems.tsx` | запуск демонстрации через общий `useScreenShareToggle` (вместо `captureOptions: { audio: true, selfBrowserSurface: 'include' }`) |
| `src/frontend/src/features/notifications/NotificationType.ts`, `components/ToastRegion.tsx` | уведомление «Звук экрана не передаётся» |
| `src/frontend/src/primitives/index.ts`, `src/frontend/src/utils/storageKeys.tsx` | экспорт `Slider`, ключ localStorage |
| `src/frontend/src/i18n/init.ts`, `i18n/useLanguageLabels.ts`, `utils/languages.ts`, `index.html`, `i18next-parser.config.json` | русский язык, `fallbackLng = 'ru'`; в `index.html` ещё `?v=2` у значков, чтобы браузеры сбросили старые |
| `src/frontend/package.json` | скрипт `i18n:check-ru` |
| `src/frontend/src/features/sdk/routes/SettingsPopup.tsx` | `BrandLogo` вместо `/assets/logo.svg` |
| `src/frontend/src/layout/Footer.tsx` | `export const Footer = () => null`: футер upstream — футер французского государства |
| `src/frontend/src/components/LoginButton.tsx`, `features/home/routes/Home.tsx`, `features/recording/components/LoginPrompt.tsx` | вход без ProConnect: обычная кнопка, без prop `proConnectHint` и подгонки высоты кнопки |
| `src/frontend/src/features/home/routes/Home.tsx` | вошедшему пользователю при `calls.enabled` — история звонков (`features/calls`, загружается отдельно); главная без входа не изменилась |
| `src/frontend/src/features/rooms/livekit/components/ScreenShareErrorModal.tsx` | без ссылки на справку La Suite |
| `src/frontend/src/features/home/components/MoreLink.tsx`, `src/frontend/.env.production`, `src/frontend/.env.development` | название по умолчанию `Fusion Meet` |
| `src/frontend/src/locales/{en,fr,de,es,nl}/global.json` | удалены `footer` и `login.proconnect*` |
| `src/frontend/src/locales/{en,fr,de,es,nl}/home.json`, `sdk.json` | тексты без госслужащих, суверенитета, «бета» и Visio; удалён неиспользуемый `introSlider.beta` |
| `src/frontend/public/favicon.ico`, `favicon-16x16.png`, `favicon-32x32.png`, `apple-touch-icon.png`, `android-chrome-192x192.png`, `android-chrome-512x512.png`, `icon.png`, `public/assets/logo.svg` | значок и логотип Fusion Meet (те же имена и размеры; в `logo.svg` текст переведён в контуры) |
| `src/frontend/src/stores/userPreferences.ts` | `join_with_microphone_off`, `join_with_camera_off` (по умолчанию `false`) |
| `src/frontend/src/features/rooms/routes/Room.tsx` | `applyJoinMediaDefaults()` один раз на вход в комнату (ленивый `useState`) |
| `src/frontend/src/features/settings/components/SettingsDialog.tsx`, `tabs/GeneralTab.tsx` | переключатели «Вход во встречу» (`JoinMediaDefaultsFields`) |
| `src/summary/summary/core/locales/__init__.py` | регистрация `ru` |
| `src/frontend/panda.config.ts`, `index.html`, `src/App.tsx`, `src/styles/index.css`, `src/styles/livekit.css`, `src/utils/storageKeys.tsx`, `src/features/pip/hooks/usePictureInPicture.ts` | тёмная тема: условие `_dark` по `[data-theme=dark]` на `<html>`, семантические токены `surface.*`, `fg.*`, `border.*`, `accent.*` и значения `_dark` у `default`, `box`, `control`, `primary`, `danger`, `success`, `warning`, `focusRing`; фон и цвет `body`; скрипт в `<head>`, который ставит тему до загрузки CSS, и `<meta name="theme-color">`; переменные LiveKit на токенах (`--lk-fg` раньше ссылалась на несуществующую переменную, акценты звонка закреплены); тема в окне «Картинка в картинке»; ключ `app_theme` |
| `src/frontend/src/features/settings/components/SettingsDialog.tsx`, `tabs/GeneralTab.tsx` | блок «Оформление» после выбора языка |
| `src/frontend/src/primitives/{A,Box,Checkbox,Dialog,Hr,Select,Spinner,Switch,Tabs,Text,TextArea}.tsx`, `buttonRecipe.ts`, `menuRecipe.ts` | светлые варианты на семантических токенах, тёмные варианты звонка (`primaryDark`, `variant="dark"`) без изменений; опечатка `borderColord` в `Box` |
| `src/frontend/src/layout/{Header,Layout,Screen,SkipLink}.tsx`, `components/FeedbackBanner.tsx`, `features/home/{routes/Home,components/IntroSlider,components/LaterMeetingDialog}.tsx`, `features/settings/components/{SettingsDialogExtended,tabs/RoomsTab,tabs/ShortcutTab,tabs/layout/RowWrapper}.tsx`, `features/shortcuts/components/ShortcutBadge.tsx`, `features/chat/components/{Chat,ChatMessageBody,ChatTextArea}.tsx`, `features/participants/components/{ParticipantRow,ParticipantsCollapsibleSection,ParticipantsList,PinBadge}.tsx`, `features/recording/{components/ControlsButton,components/LoginPrompt,components/NoAccessView,components/RequestRecording,components/RowWrapper,routes/RecordingDownload}.tsx`, `features/rooms/components/{ActiveSpeaker,InviteDialog,Permissions,Rating}.tsx`, `features/rooms/livekit/components/{Admin,IsIdleDisconnectModal,Tools}.tsx`, `features/rooms/livekit/components/effects/EffectsConfiguration.tsx`, `features/rooms/livekit/components/controls/Device/{AudioLevelGauge,OutputSoundTester}.tsx`, `features/diagnostics/{components/ConnectionTestStepRow,components/ConnectionTestSummary,components/stepAppearance,routes/ConnectionTest}.ts(x)` | тёмная тема: цвета палитры заменены семантическими токенами (только стили); `ActiveSpeaker` закреплён на `blue.700`, чтобы звонок выглядел одинаково в обеих темах |
| `docs/theming.md` | описание светлой и тёмной темы, `[data-theme=dark]` для своего CSS |

## Удалённые файлы upstream

| Файл | Почему |
|---|---|
| `src/frontend/src/features/legalsTerms/` (`LegalTermsRoute.tsx`, `Accessibility.tsx`, `TermsOfService.tsx`), `src/frontend/src/locales/*/legals.json`, `accessibility.json`, `termsOfService.json` | юридические страницы DINUM о государственном сервисе Visio; адреса теперь открывают «страница не найдена» |
| `src/frontend/src/components/ProConnectButton.tsx`, `src/frontend/public/assets/proconnect-button.svg`, `proconnect-button-hovered.svg` | вход через французский государственный ProConnect |
| `src/frontend/public/assets/logo-suite-numerique.png` | логотип La Suite |
| `src/frontend/public/.well-known/windows-app-web-link` | связывал домен с Windows-приложением Visio |

Конфигурация, которая отключает пост-звонковую расшифровку через summary/WhisperX и
выжимку через LLM, задана только в `deploy/compose.yaml` (`RECORDING_WORKER_CLASSES`
без `transcript`, без `SUMMARY_SERVICE_*`). Код upstream для этого не менялся.

## Как подтянуть обновления upstream

```bash
git remote add upstream https://github.com/suitenumerique/meet.git   # один раз
git fetch upstream
git switch -c sync/upstream-$(date +%Y%m%d)
git merge upstream/main
```

1. Разрешите конфликты. Они ожидаются только в файлах из таблиц выше. Если upstream изменил
   удалённый у нас файл (конфликт modify/delete), оставьте удаление: `git rm <файл>`.
2. Переведите новые строки:
   - frontend: `node bin/check-i18n-ru` покажет недостающие ключи в `src/frontend/src/locales/ru/`;
   - backend и письма: соберите письма (`make mails-build`), затем
     `cd src/backend && python manage.py makemessages -l ru_RU` и переведите пустые и `fuzzy` строки;
   - summary: если в `LocaleStrings` появились поля, добавьте их в `ru.py`.
3. Миграции: `transcripts/0001_initial` и `calls/0001_initial` зависят от `core.0028_room_deleted_at`
   (`calls` ещё и от `transcripts.0002`) и не конфликтуют с новыми миграциями `core`. Проверьте
   `python manage.py makemigrations --check`.
4. Тесты: backend (`make test-back`), frontend (`npm run lint && npm run check && npm test && npm run build`),
   агенты (`cd src/agents && uv run pytest`), asr (`cd asr && pytest`).
5. Сверьте с upstream версии LiveKit, Egress и переменные окружения в `src/backend/meet/settings.py`
   и `compose.yml`, обновите закреплённые версии в `deploy/compose.yaml`, если upstream их поднял.
6. Влейте ветку в `main` и на сервере выполните `sudo ./deploy/up.sh`: он пересоберёт образы
   и перезапустит изменившиеся сервисы, `deploy/.env` сохранится.
