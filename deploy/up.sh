#!/usr/bin/env bash
# FusionMeet: создать deploy/.env (при первом запуске), собрать образы, поднять
# стек и дождаться прохождения healthcheck всех сервисов.
#
#   sudo ./deploy/up.sh             # настроить (если нужно), собрать и запустить
#   sudo ./deploy/up.sh --env-only  # только создать или дополнить deploy/.env
#
# Повторный запуск безопасен: существующие значения в deploy/.env сохраняются,
# дописываются только недостающие переменные и пустые секреты (и исправляются
# значения, которые новая версия больше не принимает, например удалённую модель
# распознавания).
# Переменные окружения:
#   ACME_EMAIL=...        email для Let's Encrypt без вопроса (первый запуск);
#   UP_WAIT_TIMEOUT=3600  сколько секунд ждать healthcheck (первая загрузка
#                         весов моделей asr занимает время).
set -euo pipefail

# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
# shellcheck source=host-nginx.sh
source "$(dirname "${BASH_SOURCE[0]}")/host-nginx.sh"

ENV_ONLY=false
for arg in "$@"; do
  case $arg in
    --env-only) ENV_ONLY=true ;;
    -h | --help)
      sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) die "неизвестный параметр: $arg (см. --help)" ;;
  esac
done

# Secrets generated when empty. The S3 key id uses Garage's own format
# (GK + 24 hex digits); the RPC secret must be 32 bytes in hex.
SECRET_KEYS=(
  DJANGO_SECRET_KEY POSTGRES_PASSWORD KEYCLOAK_DB_PASSWORD KEYCLOAK_ADMIN_PASSWORD
  OIDC_RP_CLIENT_SECRET REDIS_PASSWORD LIVEKIT_API_SECRET GARAGE_RPC_SECRET
  S3_ACCESS_KEY_ID S3_SECRET_ACCESS_KEY ASR_API_KEY TRANSCRIPTION_AGENT_TOKEN
)

# Recognition model removed from asr: its id in an older deploy/.env would stop asr
REMOVED_ASR_MODEL=parakeet-v3
# Its weights in the asr-models volume (onnx_engine.download_snapshot layout)
REMOVED_ASR_WEIGHTS=/models/onnx/istupakov--parakeet-tdt-0.6b-v3-onnx
# UDP buffers for LiveKit (its log warns about a small receive buffer otherwise)
SYSCTL_FILE=/etc/sysctl.d/90-fusionmeet-livekit.conf
UDP_BUFFER_BYTES=16777216

gen_secret() {
  case $1 in
    S3_ACCESS_KEY_ID) printf 'GK%s' "$(openssl rand -hex 12)" ;;
    KEYCLOAK_ADMIN_PASSWORD) openssl rand -hex 16 ;;
    *) openssl rand -hex 32 ;;
  esac
}

detect_lan_ip() {
  local ip=''
  if command -v ip >/dev/null 2>&1; then
    # source address of the default route
    ip=$(ip -4 route get 1.1.1.1 2>/dev/null |
      awk '{ for (i = 1; i < NF; i++) if ($i == "src") { print $(i + 1); exit } }')
  fi
  if [[ -z $ip ]] && command -v hostname >/dev/null 2>&1; then
    ip=$(hostname -I 2>/dev/null | awk '{ print $1 }')
  fi
  printf '%s' "$ip"
}

valid_ipv4() {
  [[ $1 =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]] || return 1
  local octet
  for octet in "${BASH_REMATCH[@]:1}"; do
    ((10#$octet <= 255)) || return 1
  done
}

valid_email() {
  [[ $1 =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]]
}

# Existing data volumes of this project (a lost .env would not match them).
existing_volumes() {
  command -v docker >/dev/null 2>&1 || return 0
  docker volume ls -q --filter "label=com.docker.compose.project=$FM_PROJECT" 2>/dev/null || true
}

# migrate_env LEGACY: fixes values of an older deploy/.env that this version
# changes. LEGACY is true when the file predates the host variables (no MEET_HOST
# before this run): one-time changes of old defaults happen only then, so a value
# chosen afterwards is kept.
migrate_env() {
  local legacy=$1
  if $legacy && [[ $(env_get "$FM_ENV_FILE" APP_TITLE) == FusionMeet ]]; then
    # env_set writes the value as is: the quotes keep the space for compose
    env_set "$FM_ENV_FILE" APP_TITLE '"Fusion Meet"'
    ok "APP_TITLE: прежнее название по умолчанию FusionMeet заменено на «Fusion Meet»"
  fi

  # asr does not start with an unknown model in ASR_MODELS or ASR_DEFAULT_MODEL
  local models item items=() kept=() removed=false default new
  models=$(env_get "$FM_ENV_FILE" ASR_MODELS)
  IFS=, read -r -a items <<<"$models"
  for item in "${items[@]}"; do
    read -r item <<<"$item" # trim
    [[ -n $item ]] || continue
    if [[ ${item,,} == "$REMOVED_ASR_MODEL" ]]; then removed=true; else kept+=("$item"); fi
  done
  if $removed; then
    # empty: the default list of compose.yaml
    env_set "$FM_ENV_FILE" ASR_MODELS "$(IFS=,; printf '%s' "${kept[*]}")"
    ok "ASR_MODELS: модель $REMOVED_ASR_MODEL убрана из списка (её больше нет в asr)"
  fi
  default=$(env_get "$FM_ENV_FILE" ASR_DEFAULT_MODEL)
  read -r default <<<"$default"
  if [[ ${default,,} == "$REMOVED_ASR_MODEL" ]]; then
    # gigaam-v3 if it stays enabled (an empty ASR_MODELS enables every model),
    # otherwise the first model left
    new=gigaam-v3
    if ((${#kept[@]} > 0)) && [[ " ${kept[*],,} " != *" $new "* ]]; then new=${kept[0],,}; fi
    env_set "$FM_ENV_FILE" ASR_DEFAULT_MODEL "$new"
    ok "ASR_DEFAULT_MODEL: $new вместо удалённой модели $REMOVED_ASR_MODEL"
  fi
}

prepare_env() {
  step "Настройки: $FM_ENV_FILE"
  command -v openssl >/dev/null 2>&1 || die "нужен openssl (sudo apt-get install -y openssl)"
  [[ -f $FM_ENV_EXAMPLE ]] || die "не найден $FM_ENV_EXAMPLE"

  umask 077
  if [[ ! -f $FM_ENV_FILE ]]; then
    local volumes
    volumes=$(existing_volumes)
    if [[ -n $volumes ]]; then
      die "deploy/.env нет, но есть данные прошлой установки (тома: $(echo "$volumes" | tr '\n' ' ')).
Новые секреты не подойдут к этим данным. Восстановите deploy/.env из резервной копии,
либо удалите старые данные: docker compose -f $FM_COMPOSE_FILE down -v"
    fi
    cp "$FM_ENV_EXAMPLE" "$FM_ENV_FILE"
    ok "создан из .env.example"
  else
    ok "файл уже есть, существующие значения сохраняются"
  fi
  chmod 600 "$FM_ENV_FILE"

  local legacy_env=false
  env_has "$FM_ENV_FILE" MEET_HOST || legacy_env=true

  # Variables added to .env.example after the first run
  local line key added=0
  while IFS= read -r line; do
    [[ $line =~ ^([A-Z_][A-Z0-9_]*)= ]] || continue
    key=${BASH_REMATCH[1]}
    if ! env_has "$FM_ENV_FILE" "$key"; then
      if ((added == 0)); then
        printf '\n# Добавлено up.sh из .env.example (%s)\n' "$(date '+%Y-%m-%d')" >>"$FM_ENV_FILE"
      fi
      printf '%s\n' "$line" >>"$FM_ENV_FILE"
      added=$((added + 1))
      ok "добавлена переменная $key"
    fi
  done <"$FM_ENV_EXAMPLE"
  migrate_env "$legacy_env"

  local generated=0
  for key in "${SECRET_KEYS[@]}"; do
    if [[ -z $(env_get "$FM_ENV_FILE" "$key") ]]; then
      env_set "$FM_ENV_FILE" "$key" "$(gen_secret "$key")"
      generated=$((generated + 1))
    fi
  done
  ((generated == 0)) || ok "сгенерировано секретов: $generated"

  local lan_ip
  lan_ip=$(env_get "$FM_ENV_FILE" LAN_IP)
  if [[ -z $lan_ip ]]; then
    lan_ip=$(detect_lan_ip)
    valid_ipv4 "$lan_ip" ||
      die "не удалось определить IP сервера в локальной сети. Укажите LAN_IP в $FM_ENV_FILE"
    env_set "$FM_ENV_FILE" LAN_IP "$lan_ip"
    ok "LAN_IP определён: $lan_ip"
  else
    valid_ipv4 "$lan_ip" || die "LAN_IP=$lan_ip в $FM_ENV_FILE не похож на IPv4-адрес"
    ok "LAN_IP: $lan_ip"
  fi

  local email
  email=$(env_get "$FM_ENV_FILE" ACME_EMAIL)
  if [[ -z $email ]]; then
    email=${ACME_EMAIL:-}
    if [[ -z $email ]]; then
      [[ -t 0 ]] ||
        die "не задан email для Let's Encrypt: запустите интерактивно или передайте ACME_EMAIL=you@example.com"
      while :; do
        read -r -p "  Email для Let's Encrypt (уведомления о сертификатах): " email
        valid_email "$email" && break
        warn "это не похоже на email, попробуйте ещё раз"
      done
    fi
    valid_email "$email" || die "ACME_EMAIL=$email не похож на email"
    env_set "$FM_ENV_FILE" ACME_EMAIL "$email"
    ok "email для Let's Encrypt сохранён: $email"
  else
    ok "email для Let's Encrypt: $email"
  fi

  local public_ip
  public_ip=$(env_get "$FM_ENV_FILE" PUBLIC_IP)
  [[ -n $(env_get "$FM_ENV_FILE" BASE_DOMAIN) ]] || die "BASE_DOMAIN пуст в $FM_ENV_FILE"
  valid_ipv4 "$public_ip" || die "PUBLIC_IP=$public_ip в $FM_ENV_FILE не похож на IPv4-адрес"
  fm_load_hosts "$FM_ENV_FILE"
  fm_check_hosts
  ok "сайт https://$FM_MEET_HOST/, вход https://$FM_KEYCLOAK_HOST/, LiveKit wss://$FM_LIVEKIT_HOST/"
  if ((${#FM_REDIRECT_HOSTS[@]} > 0)); then
    ok "перенаправляются на сайт: ${FM_REDIRECT_HOSTS[*]}"
  fi
}

# check_dns: every public name must resolve to PUBLIC_IP (or to LAN_IP, through a
# local DNS of the home network). A main name that does not resolve stops up.sh:
# Caddy would never get its certificate, and each failed attempt counts against
# the Let's Encrypt limits. A name of MEET_REDIRECT_HOSTS that does not resolve,
# or resolves to another address, is left out of this run (Caddy and the host
# nginx), with a warning.
check_dns() {
  step "DNS"
  local public_ip lan_ip item key host resolved missing=() keys=() kept=()
  public_ip=$(env_get "$FM_ENV_FILE" PUBLIC_IP)
  lan_ip=$(env_get "$FM_ENV_FILE" LAN_IP)
  for item in "MEET_HOST=$FM_MEET_HOST" "KEYCLOAK_HOST=$FM_KEYCLOAK_HOST" "LIVEKIT_HOST=$FM_LIVEKIT_HOST"; do
    key=${item%%=*}
    host=${item#*=}
    resolved=$(fm_resolve "$host")
    if [[ -z $resolved ]]; then
      missing+=("$host")
      keys+=("$key")
      fail "$host ($key) не найден в DNS"
    elif [[ $resolved != "$public_ip" && $resolved != "$lan_ip" ]]; then
      warn "$host указывает на $resolved, а PUBLIC_IP=$public_ip: сертификат не выпустится"
    else
      ok "$host -> $resolved"
    fi
  done
  if ((${#missing[@]} > 0)); then
    local records=''
    for host in "${missing[@]}"; do records+=$(printf '\n  %s  A  %s' "$host" "$public_ip"); done
    # An empty MEET_HOST is meet.<BASE_DOMAIN>, which must not stay a redirect name
    local redirect_note='' default_meet
    default_meet=meet.$(env_get "$FM_ENV_FILE" BASE_DOMAIN)
    if [[ " ${keys[*]} " == *" MEET_HOST "* && " ${FM_REDIRECT_HOSTS[*]} " == *" $default_meet "* ]]; then
      redirect_note=" (и уберите $default_meet из MEET_REDIRECT_HOSTS)"
    fi
    die "не найдены в DNS: ${missing[*]}.
Добавьте у регистратора домена A-записи (или одну запись * на все поддомены):$records
Если записи уже добавлены, подождите до 30 минут: DNS-серверы помнят прежний ответ
«такого имени нет». Затем запустите up.sh снова. Либо оставьте пустыми ${keys[*]}
в $FM_ENV_FILE$redirect_note: тогда используются имена от BASE_DOMAIN."
  fi
  for host in "${FM_REDIRECT_HOSTS[@]}"; do
    resolved=$(fm_resolve "$host")
    if [[ -z $resolved ]]; then
      warn "$host (MEET_REDIRECT_HOSTS) не найден в DNS: в этот раз пропущен. Добавьте A-запись $host -> $public_ip и запустите up.sh снова"
      continue
    fi
    if [[ $resolved != "$public_ip" && $resolved != "$lan_ip" ]]; then
      # Caddy would not get its certificate and would keep trying
      warn "$host (MEET_REDIRECT_HOSTS) указывает на $resolved, а PUBLIC_IP=$public_ip: в этот раз пропущен. Исправьте A-запись $host -> $public_ip и запустите up.sh снова"
      continue
    fi
    ok "$host -> $resolved (перенаправление на $FM_MEET_HOST)"
    kept+=("$host")
  done
  FM_REDIRECT_HOSTS=("${kept[@]}")
  FM_HOSTS=("$FM_MEET_HOST" "${FM_REDIRECT_HOSTS[@]}" "$FM_KEYCLOAK_HOST" "$FM_LIVEKIT_HOST")
  # docker compose takes a variable of the environment before deploy/.env
  export MEET_REDIRECT_HOSTS="${FM_REDIRECT_HOSTS[*]}"
}

# tune_udp_buffers: the UDP buffer limits LiveKit asks for, applied now and kept
# after a reboot (SYSCTL_FILE). A larger value already set is never lowered.
# Sets UDP_BUFFERS_CHANGED when the running values changed.
UDP_BUFFERS_CHANGED=false
tune_udp_buffers() {
  step "UDP-буферы для LiveKit"
  local key current content before='' after='' values
  content="# FusionMeet (deploy/up.sh): UDP buffers for LiveKit (WebRTC media)"
  for key in net.core.rmem_max net.core.wmem_max; do
    current=$(sysctl -n "$key" 2>/dev/null || true)
    [[ $current =~ ^[0-9]+$ ]] || current=0
    before+="$current "
    content+=$'\n'"$key = $((current > UDP_BUFFER_BYTES ? current : UDP_BUFFER_BYTES))"
  done
  if [[ $(cat "$SYSCTL_FILE" 2>/dev/null) != "$content" ]] &&
    ! printf '%s\n' "$content" 2>/dev/null >"$SYSCTL_FILE"; then
    warn "не удалось записать $SYSCTL_FILE: UDP-буферы не изменены (видео может подтормаживать)"
    return 0
  fi
  if ! sysctl -q -p "$SYSCTL_FILE" >/dev/null 2>&1; then
    warn "sysctl не применил $SYSCTL_FILE (нет доступа к настройкам ядра?): UDP-буферы не изменены"
    return 0
  fi
  for key in net.core.rmem_max net.core.wmem_max; do
    after+="$(sysctl -n "$key" 2>/dev/null || true) "
  done
  read -r -a values <<<"$after"
  if [[ $after != "$before" ]]; then
    UDP_BUFFERS_CHANGED=true
    ok "UDP-буферы увеличены: net.core.rmem_max=${values[0]}, net.core.wmem_max=${values[1]} (сохранятся после перезагрузки: $SYSCTL_FILE)"
  else
    ok "net.core.rmem_max=${values[0]}, net.core.wmem_max=${values[1]} ($SYSCTL_FILE)"
  fi
}

print_summary() {
  local redirects=''
  if ((${#FM_REDIRECT_HOSTS[@]} > 0)); then
    redirects=$(printf '\n  Перенаправляются:    %s' "${FM_REDIRECT_HOSTS[*]}")
  fi
  cat <<EOF

${FM_GREEN}${FM_BOLD}FusionMeet запущен.${FM_RESET}

  Вход:                https://$FM_MEET_HOST/$redirects
  Keycloak (админка):  https://$FM_KEYCLOAK_HOST/admin/
                       логин admin, пароль: KEYCLOAK_ADMIN_PASSWORD в $FM_ENV_FILE

Пользователи регистрируются сами по ссылке «Регистрация» на странице входа
(если KEYCLOAK_REGISTRATION_ALLOWED=true), или их создаёт администратор:

  sudo $FM_DEPLOY_DIR/add-user.sh <логин> <email>

Гости заходят в комнату по ссылке без учётной записи.
Состояние:  sudo docker compose -f $FM_COMPOSE_FILE ps
Журналы:    sudo docker compose -f $FM_COMPOSE_FILE logs -f <сервис>
EOF
}

prepare_env
if $ENV_ONLY; then
  ok "готово (--env-only): стек не запускался"
  exit 0
fi

step "Проверка Docker"
require_docker
ok "$(docker --version)"
ok "$(docker compose version)"
if ! docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q nvidia; then
  warn "в Docker не зарегистрирован runtime nvidia: сервис asr может не получить GPU."
  note "Запустите sudo $FM_DEPLOY_DIR/install.sh и выполните его рекомендации."
fi
docker buildx version >/dev/null 2>&1 ||
  die "нет плагина docker buildx, без него образы не соберутся. Запустите: sudo $FM_DEPLOY_DIR/install.sh"

check_dns

step "Обратный прокси"
hn_resolve_mode
fm_compose config --quiet
if [[ $HN_MODE == nginx ]]; then
  # Names taken by other sites stop up.sh before the long build; nginx itself
  # changes only right before "up" (hn_before_up)
  hn_check_names "${FM_HOSTS[@]}"
  ok "имена FusionMeet не заняты другими сайтами nginx сервера"
else
  # Back from PROXY_MODE=nginx: the forwarding of the host nginx is not needed
  for path in "/etc/nginx/conf.d/$HN_CONF_NAME" "/etc/nginx/sites-enabled/$HN_CONF_NAME"; do
    if [[ -e $path ]]; then
      rm -f "$path" "/etc/nginx/sites-available/$HN_CONF_NAME"
      nginx -t >/dev/null 2>&1 && systemctl reload nginx
      ok "удалены настройки FusionMeet из nginx сервера"
    fi
  done
fi

tune_udp_buffers
# LiveKit reads the limits when it starts: restart it after "up", unless "up"
# creates its container anew anyway
livekit_before=''
if $UDP_BUFFERS_CHANGED; then
  livekit_before=$(fm_compose ps --quiet --status running livekit 2>/dev/null || true)
fi

step "Сборка образов (frontend, backend, agents, asr)"
fm_compose build

step "Запуск и ожидание healthcheck всех сервисов"
if [[ $HN_MODE == nginx ]]; then hn_before_up "${FM_HOSTS[@]}"; fi
note "Первый запуск дольше: Keycloak собирает себя, asr скачивает веса моделей распознавания (~2,5 ГБ)."
note "Ход загрузки моделей: sudo docker compose -f $FM_COMPOSE_FILE logs -f asr"
if ! fm_compose up --detach --wait --wait-timeout "${UP_WAIT_TIMEOUT:-3600}" --remove-orphans; then
  fm_compose ps --all || true
  die "не все сервисы прошли healthcheck. Журнал сервиса:
  sudo docker compose -f $FM_COMPOSE_FILE logs --tail 100 <сервис>
Подсказки по типичным проблемам: $FM_DEPLOY_DIR/README.md (раздел «Если что-то не работает»)."
fi
# Weights of the removed recognition model (about 2.5 GB in the asr-models volume)
if fm_compose exec -T asr test -d "$REMOVED_ASR_WEIGHTS" 2>/dev/null; then
  if fm_compose exec -T asr rm -rf "$REMOVED_ASR_WEIGHTS"; then
    ok "удалены веса модели $REMOVED_ASR_MODEL (около 2,5 ГБ в томе ${FM_PROJECT}_asr-models)"
  else
    warn "не удалось удалить веса модели $REMOVED_ASR_MODEL: $REMOVED_ASR_WEIGHTS в контейнере asr"
  fi
fi
if [[ -n $livekit_before && $(fm_compose ps --quiet livekit 2>/dev/null) == "$livekit_before" ]]; then
  fm_compose restart livekit >/dev/null
  ok "LiveKit перезапущен с новыми UDP-буферами (идущие звонки переподключаются сами)"
fi
# The Keycloak steps only need the keycloak container: they come before the wait
# for certificates, which may stop up.sh
step "Регистрация в Keycloak"
registration=$(env_get "$FM_ENV_FILE" KEYCLOAK_REGISTRATION_ALLOWED)
registration=${registration:-false}
[[ $registration == true || $registration == false ]] ||
  die "KEYCLOAK_REGISTRATION_ALLOWED=$registration в $FM_ENV_FILE: допустимо true или false"
# The realm JSON is imported only on the first start: apply the setting to the
# running realm on every run instead.
# shellcheck disable=SC2016  # expanded by bash inside the container
kc_admin '"$kcadm" update realms/meet --config "$cfg" -s "registrationAllowed=$1"' "$registration" ||
  die "не удалось изменить настройку регистрации в Keycloak (см. сообщение выше)"
if [[ $registration == true ]]; then
  ok "самостоятельная регистрация включена (ссылка «Регистрация» на странице входа)"
else
  ok "самостоятельная регистрация выключена: пользователей создаёт add-user.sh"
fi

step "Адреса входа и название в Keycloak"
# Like the registration setting: the client URLs and the names come from the
# realm JSON only on the first start, so a changed MEET_HOST (or BASE_DOMAIN, new
# IP) would break the login and a changed APP_TITLE would not show. The name is
# APP_TITLE of the keycloak container (compose.yaml), the value compose resolved.
meet_url="https://$FM_MEET_HOST"
# shellcheck disable=SC2016  # expanded by bash inside the container
title=$(kc_admin '
client_id=$("$kcadm" get clients --config "$cfg" -r meet -q clientId=meet --fields id --format csv --noquotes)
[ -n "$client_id" ] || { echo "клиент meet не найден в realm meet" >&2; exit 1; }
"$kcadm" update "clients/$client_id" --config "$cfg" -r meet \
  -s "rootUrl=$1" -s "redirectUris=[\"$1/*\"]" -s "webOrigins=[\"$1\"]" \
  -s "attributes.\"post.logout.redirect.uris\"=$1/*" -s "name=$APP_TITLE"
"$kcadm" update realms/meet --config "$cfg" -s "displayName=$APP_TITLE"
echo "$APP_TITLE"
' "$meet_url") || die "не удалось обновить клиент meet и realm в Keycloak (см. сообщение выше)"
ok "вход через Keycloak возвращает на $meet_url"
ok "название на странице входа: ${title##*$'\n'}"

if [[ $HN_MODE == nginx ]]; then
  step "HTTPS через nginx сервера"
  hn_after_up "$FM_MEET_HOST $FM_KEYCLOAK_HOST $FM_LIVEKIT_HOST" "${FM_HOSTS[@]}"
fi

fm_compose ps
print_summary
