# Развёртывание FusionMeet на одном сервере

Весь сервис (видеозвонки, запись, расшифровка речи на GigaAM) работает на одном
Linux-сервере с одной видеокартой NVIDIA через `docker compose`. Стек запускается
как отдельный проект compose `fusionmeet`: его контейнеры, тома и сеть называются
`fusionmeet-…` и не пересекаются с другими контейнерами на сервере (например, с vLLM).

Адреса строятся из `BASE_DOMAIN` (по умолчанию `213-141-132-74.sslip.io`):

| Адрес | Что это |
|---|---|
| `https://meet.<BASE_DOMAIN>/` | интерфейс видеозвонков |
| `https://id.<BASE_DOMAIN>/` | Keycloak: вход, учётные записи, админка `/admin/` |
| `wss://livekit.<BASE_DOMAIN>/` | сигнальный сервер LiveKit |

## Быстрый старт

```bash
git clone <ваш форк> fusionmeet && cd fusionmeet
sudo ./deploy/install.sh                      # Docker, NVIDIA Container Toolkit, проверки
sudo ./deploy/up.sh                           # спросит email для Let's Encrypt, соберёт и запустит
sudo ./deploy/add-user.sh ivan ivan@example.ru # пользователь и временный пароль
```

Откройте `https://meet.<BASE_DOMAIN>/` и войдите. При первом входе Keycloak попросит
сменить временный пароль и указать имя и фамилию: их видят участники звонка.

## Что запускается

| Сервис | Образ | Назначение |
|---|---|---|
| `caddy` | `caddy:2.11.7` | HTTPS для трёх адресов, сертификаты Let's Encrypt (HTTP-01) |
| `frontend` | `fusionmeet/frontend:local`, собирается из репозитория | интерфейс (nginx) и маршрутизация `/api`, `/admin`, `/static`, `/media/recordings/` |
| `backend` | `fusionmeet/backend:local`, собирается из репозитория | Django API |
| `backend-migrate` | `fusionmeet/backend:local` (образ `backend`) | разовый запуск миграций базы перед `backend` |
| `postgres` | `postgres:16.15-alpine3.24` | базы приложения и Keycloak |
| `redis` | `redis:7.2.16-alpine` | кеш, сессии, состояние LiveKit и Egress |
| `garage` | `dxflrs/garage:v2.4.1` | S3-хранилище записей, расшифровок и метаданных (бакет создаётся сам) |
| `keycloak` | `quay.io/keycloak/keycloak:26.8.1` | учётные записи, realm `meet` импортируется при первом запуске |
| `livekit` | `livekit/livekit-server:v1.13.6` | медиасервер (SFU), работает в сети хоста |
| `egress` | `livekit/egress:v1.11.0` | запись встречи в MP4 |
| `agents-transcriber` | `fusionmeet/agents:local`, собирается из `src/agents` | субтитры и расшифровка по участникам (STT-провайдер gigaam) |
| `agents-metadata-collector` | `fusionmeet/agents:local` (образ `agents-transcriber`) | агент сбора метаданных встречи |
| `asr` | `fusionmeet/asr:local`, собирается из `asr/` | распознавание речи GigaAM Multilingual на GPU, наружу не открыт |

Четыре образа `fusionmeet/…:local` собираются на сервере (`up.sh` вызывает
`docker compose build`), каждый один раз. Из Docker Hub они не скачиваются
(`pull_policy: never`): это локальные имена, и в Docker Hub под ними мог бы
оказаться чужой образ.

Вместо MinIO используется Garage: образ `minio/minio` больше не публикуется на
Docker Hub, а Garage уже используется в dev-стеке upstream (`compose.yml`).

Секреты хранятся только в `deploy/.env` (создаёт `up.sh`, файл в `.gitignore`).
Описание всех переменных — в `deploy/.env.example`.

## Порты

| Порт | Кто слушает | Проброс на роутере | Зачем |
|---|---|---|---|
| 80/tcp | Caddy | да | выпуск сертификатов Let's Encrypt (HTTP-01), переадресация на HTTPS |
| 443/tcp | Caddy | да | HTTPS для meet, id и livekit (WebSocket) |
| 443/udp | Caddy | да | HTTP/3 (без него всё работает по 443/tcp) |
| 7881/tcp | LiveKit | да | медиа по TCP, если UDP недоступен |
| 7882/udp | LiveKit | да | весь медиатрафик (аудио, видео, экран) по UDP |
| 7880/tcp | LiveKit | **нет** | API и WebSocket LiveKit; снаружи доступен только через Caddy (`livekit.<BASE_DOMAIN>`) |
| 127.0.0.1:16379/tcp | Redis | нет | только для LiveKit в сети хоста (`REDIS_HOST_PORT`) |

Про порты LiveKit из upstream-конфигурации:

- В примере upstream (`docs/examples/livekit/server.yaml`) LiveKit тоже использует
  7880/tcp (за обратным прокси), 7881/tcp и 7882/udp. Здесь 7880/tcp занят на всех
  интерфейсах сервера, потому что LiveKit работает в сети хоста. Пробрасывать его на
  роутере не нужно, но он не должен быть занят другим сервисом.
- TURN выключен, его порты не нужны: у сервера белый IP и проброшены 7881/tcp
  и 7882/udp. В upstream TURN встречается только в dev-конфигурации (3478/udp
  и 30000–30100/udp) и в helm (TURN на 443/udp). Если когда-нибудь понадобится
  TURN для сетей, где открыт только 443, это потребует отдельной настройки:
  443/udp занят HTTP/3 Caddy.
- Диапазон 50000–60000/udp из документации LiveKit не используется: при заданном
  `udp_port` весь UDP идёт через один порт 7882.

Если включён `ufw`: порты, опубликованные Docker (80, 443), он не блокирует, а
порты LiveKit в сети хоста — блокирует. Нужны правила (install.sh их печатает,
но не применяет):

```bash
sudo ufw allow 7881/tcp
sudo ufw allow 7882/udp
sudo ufw allow from 172.16.0.0/12 to any port 7880 proto tcp   # Caddy, egress и агенты -> LiveKit
```

## Домашняя сеть: NAT loopback

Имена `*.213-141-132-74.sslip.io` указывают на внешний IP. Устройство внутри
домашней сети обращается к внешнему IP своего же роутера, и это работает, только если
роутер умеет NAT loopback (hairpin NAT). Многие домашние роутеры не умеют.

- **Медиа.** LiveKit сообщает клиентам два адреса: внешний `PUBLIC_IP` и
  внутренний `LAN_IP` сервера. Устройства в домашней сети соединяются с `LAN_IP`
  напрямую, NAT loopback для медиа не нужен.
- **Сайт (HTTPS и WebSocket).** Без NAT loopback браузер в домашней сети не откроет
  `https://meet.<BASE_DOMAIN>/`. Нужна одна из мер:
  - локальный DNS на роутере (dnsmasq, OpenWrt, Keenetic, Pi-hole): все имена
    `*.213-141-132-74.sslip.io` → `LAN_IP`. Для dnsmasq:
    `address=/213-141-132-74.sslip.io/192.168.1.10`;
  - или на каждом компьютере строки в hosts (`/etc/hosts`,
    `C:\Windows\System32\drivers\etc\hosts`):
    ```
    192.168.1.10 meet.213-141-132-74.sslip.io id.213-141-132-74.sslip.io livekit.213-141-132-74.sslip.io
    ```
  - или включить NAT loopback на роутере, если он это умеет.

  Сертификат Let's Encrypt подходит и при обращении по `LAN_IP`, потому что имя то же.
- **Сами контейнеры на сервере** от этого не зависят: внутри сети compose эти три
  имени указывают прямо на Caddy, а LiveKit отправляет вебхуки через 127.0.0.1.

Закрепите за сервером постоянный адрес в DHCP роутера. Если `LAN_IP` всё же
изменился, исправьте его в `deploy/.env` (или очистите значение, и `up.sh`
определит адрес заново) и запустите `sudo ./deploy/up.sh`.

## Кто что может

- **Гости** заходят в комнату по ссылке без учётной записи. Новые комнаты по
  умолчанию открытые (`RESOURCE_DEFAULT_ACCESS_LEVEL=public`); владелец может
  включить зал ожидания в настройках комнаты.
- **Пользователи с учётной записью** создают комнаты. Самостоятельная регистрация
  в Keycloak управляется `KEYCLOAK_REGISTRATION_ALLOWED` в `deploy/.env`
  (по умолчанию `true`: на странице входа есть ссылка «Регистрация»; учётную запись
  может создать любой, кто откроет сайт). При `false` пользователей создаёт
  администратор через `add-user.sh` или в админке Keycloak. Новое значение
  применяется командой `sudo ./deploy/up.sh`.
- **Запись и расшифровку** включают только владелец комнаты и её модераторы.

## Скрипты

| Команда | Что делает |
|---|---|
| `sudo ./deploy/install.sh` | проверяет драйвер NVIDIA; ставит Docker и NVIDIA Container Toolkit, если их нет; проверяет, что контейнеры видят GPU и что порты свободны; показывает свободную видеопамять. Существующие контейнеры не трогает |
| `sudo ./deploy/up.sh` | при первом запуске создаёт `deploy/.env` (секреты, `LAN_IP`, email для Let's Encrypt); собирает образы, запускает стек и ждёт healthcheck всех сервисов. Повторный запуск сохраняет `.env` и обновляет контейнеры |
| `sudo ./deploy/up.sh --env-only` | только создать или дополнить `deploy/.env` |
| `sudo ./deploy/add-user.sh <логин> <email>` | создаёт пользователя и печатает временный пароль; для существующего пользователя задаёт новый временный пароль |

Первый `up.sh` идёт долго: сборка четырёх образов, первая сборка Keycloak и
загрузка весов модели (около 2,3 ГБ) в том `fusionmeet_asr-models`. По умолчанию
`up.sh` ждёт healthcheck до 60 минут (`sudo UP_WAIT_TIMEOUT=7200 ./deploy/up.sh`, чтобы
ждать дольше). Ход загрузки модели: `sudo docker compose -f deploy/compose.yaml logs -f asr`.

## Повседневные команды

Команды выполняются из корня репозитория. `docker compose` сам читает `deploy/.env`.

```bash
sudo docker compose -f deploy/compose.yaml ps                    # состояние и healthcheck
sudo docker compose -f deploy/compose.yaml logs -f backend       # журнал сервиса
sudo docker compose -f deploy/compose.yaml logs --since 1h asr
sudo docker compose -f deploy/compose.yaml restart agents-transcriber
sudo docker compose -f deploy/compose.yaml down                  # остановить (данные сохраняются)
git pull && sudo ./deploy/up.sh                                  # обновление
```

Не выполняйте `down -v`: эта команда удаляет тома с базами, записями и сертификатами.

Перед обновлением или остановкой дождитесь окончания записей: `egress` при
остановке дописывает текущие записи, но ждёт не дольше 10 минут.

## Данные и резервные копии

| Том | Содержимое |
|---|---|
| `fusionmeet_postgres-data` | базы приложения и Keycloak |
| `fusionmeet_garage-meta`, `fusionmeet_garage-data` | записи (MP4), расшифровки (`.json`, `.md`), метаданные |
| `fusionmeet_caddy-data` | сертификаты Let's Encrypt |
| `fusionmeet_asr-models` | кеш весов модели (можно не копировать) |
| `fusionmeet_redis-data` | сессии и кеш (можно не копировать) |

Храните резервную копию `deploy/.env` вместе с данными: пароли баз и ключи S3 из
него уже записаны в тома. Если `.env` потерян, а тома остались, `up.sh` откажется
создавать новые секреты.

Пример копии базы:

```bash
sudo docker compose -f deploy/compose.yaml exec -T postgres pg_dumpall -U meet > fusionmeet-db.sql
```

## Если что-то не работает

**Сначала:** `sudo docker compose -f deploy/compose.yaml ps` показывает, какой
сервис не прошёл healthcheck, а `logs --tail 100 <сервис>` — почему.

### Драйвер NVIDIA

asr использует PyTorch с CUDA 12.8 (колёса `cu128` со своими библиотеками CUDA),
от системы нужен только драйвер. `install.sh` проверяет его версию:

| Драйвер | Результат |
|---|---|
| ниже 525.60.13 | ошибка: драйвер не поддерживает CUDA 12, asr не запустится |
| 525.60.13 – 569.x | предупреждение: asr должен работать через совместимость минорных версий CUDA (CUDA minor version compatibility), но на таких драйверах не проверялся |
| 570 и новее | ок: CUDA 12.8 выпущена с драйвером 570, совместимость минорных версий не нужна |

Обновлять драйвер ради asr не обязательно: обновление останавливает всё, что
работает на GPU, в том числе vLLM. Базовый образ CUDA разрешает запуск на
видеокартах GeForce только с драйвером 570+ (`NVIDIA_REQUIRE_CUDA`), поэтому образ
asr отключает эту проверку (`NVIDIA_DISABLE_REQUIRE=true` в `asr/Dockerfile`).
По документации NVIDIA у совместимости минорных версий есть ограничения: не
работает JIT-компиляция PTX, и функции CUDA, которым нужен драйвер новее, завершаются
ошибкой `cudaErrorCallRequiresNewerDriver`. Сборка PyTorch cu128 содержит готовый
код для RTX 3090 (sm_86), PTX ей не нужен. Если asr на таком драйвере всё же не
стартует и в его журнале ошибка CUDA про драйвер или PTX, обновите драйвер до 570+
в удобное время, когда vLLM можно остановить.

### Не хватает видеопамяти (asr)

- asr берёт `min(ASR_MAX_VRAM_GB, свободно)`. Свободную память он меряет уже после
  создания своего контекста CUDA (около 0,5 ГБ). `sudo ./deploy/install.sh`
  показывает этот бюджет и какая модель в него поместится: `large_ctc` около 3 ГБ,
  `ctc` около 1,5 ГБ. Если `large_ctc` не помещается или падает с нехваткой памяти
  на прогреве, asr сам переходит на `ctc`.
- В журнале `asr` видно, какая модель загружена. Если на `large_ctc` не хватает
  памяти, asr сам загружает запасную `ctc` и пишет об этом предупреждение. Если не
  хватает и на неё, asr завершается с ошибкой, где указано, сколько памяти свободно
  и сколько нужно.
- Что можно сделать: уменьшить `--gpu-memory-utilization` у vLLM (каждые 0,01 —
  около 0,24 ГБ на RTX 3090) и перезапустить vLLM в удобное время; или уменьшить
  `ASR_MAX_VRAM_GB` в `deploy/.env` (`large_ctc` нужно около 3 ГБ, `ctc` около
  1,5 ГБ). Затем: `sudo docker compose -f deploy/compose.yaml up -d asr`.
- `docker: could not select device driver "nvidia"`: Docker запущен раньше, чем
  установлен NVIDIA Container Toolkit. Запустите `sudo ./deploy/install.sh`: он
  включит live-restore и предложит перезапустить Docker без остановки контейнеров.

### Порты 80 и 443 заняты nginx сервера

Ничего делать не нужно: при `PROXY_MODE=auto` (по умолчанию) `up.sh` видит nginx
на 80/443 и встаёт за него (`PROXY_MODE=nginx` записывается в `deploy/.env`):

- Caddy слушает только `127.0.0.1:18080` и `127.0.0.1:18443`
  (`CADDY_LOCAL_HTTP_PORT`, `CADDY_LOCAL_HTTPS_PORT`).
- `up.sh` кладёт в nginx файл `fusionmeet.conf` (в `conf.d` или `sites-available`).
  Файл пересылает в Caddy только `meet.`, `id.` и `livekit.<BASE_DOMAIN>`, остальные
  сайты nginx не меняются. Перед перезагрузкой nginx файл проверяется `nginx -t`, при
  ошибке прежние настройки восстанавливаются.
- Сертификаты Let's Encrypt получает и продлевает Caddy (HTTP-01 через порт 80 nginx).
  nginx берёт их из тома Caddy и перечитывает раз в сутки
  (`fusionmeet-nginx-reload.timer`).
- HTTP/3 (443/udp) в этом режиме не используется.

### Сертификаты не выпускаются

- Журнал: `sudo docker compose -f deploy/compose.yaml logs caddy | grep -i -E 'error|acme'`.
- Порты 80/tcp и 443/tcp должны быть проброшены на сервер, а `meet.<BASE_DOMAIN>`
  должен указывать на `PUBLIC_IP` (`getent hosts meet.<BASE_DOMAIN>`).
- После нескольких неудачных попыток Let's Encrypt временно ограничивает выпуск
  (около часа). Для экспериментов можно указать в `deploy/.env` тестовый
  `ACME_CA` (см. комментарий там), но с ним сайт не работает: верните рабочий адрес,
  удалите тестовые сертификаты (`sudo docker compose -f deploy/compose.yaml down`,
  затем `sudo docker volume rm fusionmeet_caddy-data`) и запустите `up.sh`.

### Нет звука или видео

- Проверьте проброс 7881/tcp и 7882/udp на `LAN_IP` сервера и правила `ufw` (выше).
- Из домашней сети: нужен локальный DNS или NAT loopback (раздел выше).
- Журнал: `sudo docker compose -f deploy/compose.yaml logs livekit`.
- LiveKit советует увеличить UDP-буферы ядра:
  `sudo sysctl -w net.core.rmem_max=16777216 net.core.wmem_max=16777216`
  (для постоянства — файл в `/etc/sysctl.d/`).

### Вход через Keycloak

- Realm `meet` (`deploy/keycloak/meet-realm.json`) импортируется **только при первом
  запуске**. Последующие правки файла не применяются: меняйте настройки в админке
  `https://id.<BASE_DOMAIN>/admin/` (логин `admin`, пароль `KEYCLOAK_ADMIN_PASSWORD`
  из `deploy/.env`).
- `KEYCLOAK_ADMIN_PASSWORD` задаёт пароль администратора тоже только при первом
  запуске. Если сменить пароль в админке, обновите его и в `deploy/.env`, иначе
  `add-user.sh` не сможет войти.
- Восстановление пароля по email выключено (в Keycloak не настроен SMTP): новый
  временный пароль выдаёт `add-user.sh`.

### Прочее

- **Garage не стартует** с ошибкой про «secret key different»: значения
  `S3_ACCESS_KEY_ID` и `S3_SECRET_ACCESS_KEY` в `deploy/.env` изменились после первого
  запуска. Верните прежние.
- **Запись не начинается**: `logs egress`. Egress запускает Chrome; на сервере
  с небольшим числом ядер он может отказываться от задач по загрузке CPU.
- **Письма**: без `SMTP_HOST` письма только выводятся в журнал `backend`. После
  настройки SMTP в `deploy/.env` выполните `sudo ./deploy/up.sh`.
