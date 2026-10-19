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
| `src/mail/mjml/transcript_ready.mjml` | письмо инициатору о готовой расшифровке |
| `src/frontend/src/features/transcription/` | кнопки «Расшифровка» и «Запись», индикатор, страница «Мои записи и расшифровки» |
| `src/frontend/src/primitives/Slider.tsx`, `src/frontend/src/stores/participantAudio.ts`, компоненты громкости в `features/participants`, `features/participantTile`, `features/rooms` | громкость участников |
| `src/frontend/src/features/rooms/livekit/components/ConnectionStateToast.tsx` | переводимая замена уведомления о подключении из `@livekit/components-react` (там тексты зашиты по-английски) |
| `src/frontend/src/locales/*/transcription.json`, `*/participantAudio.json` | строки новых функций (отдельные пространства имён, чтобы не трогать файлы upstream) |
| `src/frontend/src/locales/ru/`, `src/backend/locale/ru_RU/`, `src/summary/summary/core/locales/ru.py` | русская локализация |
| `bin/check-i18n-ru` | проверка, что ключи `ru` совпадают с `en` |
| `docs/features/gigaam-transcription.md` | описание расшифровки по участникам |
| `deploy/` (кроме `deploy/paas/`) | docker compose для одного сервера с GPU, скрипты установки |
| `UPSTREAM.md` | этот файл |

## Изменённые файлы upstream

| Файл | Изменение |
|---|---|
| `src/agents/multi_user_transcriber.py` | ветка `STT_PROVIDER=gigaam`, приём финальных фраз, управление расшифровкой по метаданным комнаты, модель распознавания комнаты, устойчивое закрытие сессий |
| `src/agents/pyproject.toml`, `src/agents/uv.lock` | `tzdata`, `pytest`, `pytest-asyncio` в dev, настройки pytest |
| `src/backend/meet/settings.py` | приложение `transcripts` и настройки `TRANSCRIPTION_*`; `ru-ru` первым в `LANGUAGES`, `LANGUAGE_CODE = "ru-ru"` (в конфигурации `Test` оставлен `en-us`, потому что тесты upstream проверяют английские тексты) |
| `src/backend/meet/urls.py` | подключение `transcripts.urls` |
| `src/backend/core/api/__init__.py` | `transcription.enabled` в `/api/v1.0/config/` |
| `src/frontend/src/api/useConfig.ts` | тип `transcription` в конфигурации |
| `src/frontend/src/routes.ts` | маршрут `/my-recordings` |
| `src/frontend/src/layout/Header.tsx` | пункт «Мои записи и расшифровки» в меню пользователя |
| `src/frontend/src/features/rooms/livekit/prefabs/ControlBar/DesktopControlBar.tsx`, `MobileControlBar.tsx` | кнопки «Расшифровка» и «Запись» |
| `src/frontend/src/features/rooms/livekit/prefabs/VideoConference.tsx` | провайдер расшифровки, синхронизатор громкости, переводимый `ConnectionStateToast` |
| `src/frontend/src/features/pip/components/PipView.tsx` | переводимый `ConnectionStateToast` |
| `src/frontend/src/features/rooms/livekit/components/controls/StartMediaButton.tsx` | перевод «Start Audio» и «Start Video» |
| `src/frontend/src/locales/{en,fr,de,es,nl}/rooms.json` | ключи `controls.startMedia.*` и `connectionState.*` для двух строк выше |
| `src/frontend/src/features/rooms/components/Conference.tsx` | `webAudioMix` там, где браузер поддерживает усиление выше 100 % |
| `src/frontend/src/features/participants/components/menu/ParticipantMenu.tsx`, `ParticipantMenuButton.tsx` | ползунок громкости и «Выключить звук для меня» |
| `src/frontend/src/features/participantTile/components/ParticipantMetadata.tsx`, `participantTileFocus/ParticipantTileFocus.tsx` | кнопка громкости на плитке и значок «звук выключен для вас» |
| `src/frontend/src/primitives/index.ts`, `src/frontend/src/utils/storageKeys.tsx` | экспорт `Slider`, ключ localStorage |
| `src/frontend/src/i18n/init.ts`, `i18n/useLanguageLabels.ts`, `utils/languages.ts`, `index.html`, `i18next-parser.config.json` | русский язык, `fallbackLng = 'ru'` |
| `src/frontend/package.json` | скрипт `i18n:check-ru` |
| `src/summary/summary/core/locales/__init__.py` | регистрация `ru` |

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

1. Разрешите конфликты. Они ожидаются только в файлах из таблицы выше.
2. Переведите новые строки:
   - frontend: `node bin/check-i18n-ru` покажет недостающие ключи в `src/frontend/src/locales/ru/`;
   - backend и письма: соберите письма (`make mails-build`), затем
     `cd src/backend && python manage.py makemessages -l ru_RU` и переведите пустые и `fuzzy` строки;
   - summary: если в `LocaleStrings` появились поля, добавьте их в `ru.py`.
3. Миграции: `transcripts/0001_initial` зависит от `core.0028_room_deleted_at` и не
   конфликтует с новыми миграциями `core`. Проверьте `python manage.py makemigrations --check`.
4. Тесты: backend (`make test-back`), frontend (`npm run lint && npm run check && npm test && npm run build`),
   агенты (`cd src/agents && uv run pytest`), asr (`cd asr && pytest`).
5. Сверьте с upstream версии LiveKit, Egress и переменные окружения в `src/backend/meet/settings.py`
   и `compose.yml`, обновите закреплённые версии в `deploy/compose.yaml`, если upstream их поднял.
6. Влейте ветку в `main` и на сервере выполните `sudo ./deploy/up.sh`: он пересоберёт образы
   и перезапустит изменившиеся сервисы, `deploy/.env` сохранится.
