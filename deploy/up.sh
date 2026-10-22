#!/usr/bin/env bash
# FusionMeet: создать deploy/.env (при первом запуске), собрать образы, поднять
# стек и дождаться прохождения healthcheck всех сервисов.
#
#   sudo ./deploy/up.sh             # настроить (если нужно), собрать и запустить
#   sudo ./deploy/up.sh --env-only  # только создать или дополнить deploy/.env
#
# Повторный запуск безопасен: существующие значения в deploy/.env сохраняются,
# дописываются только недостающие переменные и пустые секреты.
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
      sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
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

  local base_domain public_ip
  base_domain=$(env_get "$FM_ENV_FILE" BASE_DOMAIN)
  public_ip=$(env_get "$FM_ENV_FILE" PUBLIC_IP)
  [[ -n $base_domain ]] || die "BASE_DOMAIN пуст в $FM_ENV_FILE"
  valid_ipv4 "$public_ip" || die "PUBLIC_IP=$public_ip в $FM_ENV_FILE не похож на IPv4-адрес"
  local resolved
  resolved=$(getent ahostsv4 "meet.$base_domain" 2>/dev/null | awk 'NR == 1 { print $1 }' || true)
  if [[ -n $resolved && $resolved != "$public_ip" && $resolved != "$lan_ip" ]]; then
    warn "meet.$base_domain указывает на $resolved, а PUBLIC_IP=$public_ip: сертификаты не выпустятся"
  fi
}

print_summary() {
  local base_domain
  base_domain=$(env_get "$FM_ENV_FILE" BASE_DOMAIN)
  cat <<EOF

${FM_GREEN}${FM_BOLD}FusionMeet запущен.${FM_RESET}

  Вход:                https://meet.$base_domain/
  Keycloak (админка):  https://id.$base_domain/admin/
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

step "Обратный прокси"
hn_resolve_mode
fm_compose config --quiet
BASE_DOMAIN_VALUE=$(env_get "$FM_ENV_FILE" BASE_DOMAIN)
HOSTS=("meet.$BASE_DOMAIN_VALUE" "id.$BASE_DOMAIN_VALUE" "livekit.$BASE_DOMAIN_VALUE")
if [[ $HN_MODE == nginx ]]; then
  hn_before_up "${HOSTS[@]}"
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

step "Сборка образов (frontend, backend, agents, asr)"
fm_compose build

step "Запуск и ожидание healthcheck всех сервисов"
note "Первый запуск дольше: Keycloak собирает себя, asr скачивает веса моделей распознавания (~5 ГБ)."
note "Ход загрузки моделей: sudo docker compose -f $FM_COMPOSE_FILE logs -f asr"
if ! fm_compose up --detach --wait --wait-timeout "${UP_WAIT_TIMEOUT:-3600}" --remove-orphans; then
  fm_compose ps --all || true
  die "не все сервисы прошли healthcheck. Журнал сервиса:
  sudo docker compose -f $FM_COMPOSE_FILE logs --tail 100 <сервис>
Подсказки по типичным проблемам: $FM_DEPLOY_DIR/README.md (раздел «Если что-то не работает»)."
fi
if [[ $HN_MODE == nginx ]]; then
  step "HTTPS через nginx сервера"
  hn_after_up "${HOSTS[@]}"
fi
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

step "Адреса входа в Keycloak"
# Like the registration setting: the client URLs come from the realm JSON only on
# the first start, so a changed BASE_DOMAIN (new IP) would break the login.
meet_url="https://meet.$(env_get "$FM_ENV_FILE" BASE_DOMAIN)"
# shellcheck disable=SC2016  # expanded by bash inside the container
kc_admin '
client_id=$("$kcadm" get clients --config "$cfg" -r meet -q clientId=meet --fields id --format csv --noquotes)
[ -n "$client_id" ] || { echo "клиент meet не найден в realm meet" >&2; exit 1; }
"$kcadm" update "clients/$client_id" --config "$cfg" -r meet \
  -s "rootUrl=$1" -s "redirectUris=[\"$1/*\"]" -s "webOrigins=[\"$1\"]" \
  -s "attributes.\"post.logout.redirect.uris\"=$1/*"
' "$meet_url" || die "не удалось обновить адреса клиента meet в Keycloak (см. сообщение выше)"
ok "вход через Keycloak возвращает на $meet_url"

fm_compose ps
print_summary
