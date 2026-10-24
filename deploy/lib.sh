# shellcheck shell=bash
# Shared helpers of the FusionMeet deploy scripts (install.sh, up.sh,
# add-user.sh). Sourced, not executed.

FM_DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FM_ENV_FILE="$FM_DEPLOY_DIR/.env"
FM_ENV_EXAMPLE="$FM_DEPLOY_DIR/.env.example"
FM_COMPOSE_FILE="$FM_DEPLOY_DIR/compose.yaml"
# Compose project name (top-level "name:" of compose.yaml)
FM_PROJECT=fusionmeet
export FM_DEPLOY_DIR FM_ENV_FILE FM_ENV_EXAMPLE FM_COMPOSE_FILE FM_PROJECT

if [[ -t 1 ]]; then
  FM_BOLD=$'\033[1m' FM_RED=$'\033[31m' FM_GREEN=$'\033[32m'
  FM_YELLOW=$'\033[33m' FM_RESET=$'\033[0m'
else
  FM_BOLD='' FM_RED='' FM_GREEN='' FM_YELLOW='' FM_RESET=''
fi

step() { printf '\n%s==> %s%s\n' "$FM_BOLD" "$*" "$FM_RESET"; }
ok() { printf '  %s[ok]%s %s\n' "$FM_GREEN" "$FM_RESET" "$*"; }
note() { printf '       %s\n' "$*"; }
warn() { printf '  %s[!]%s %s\n' "$FM_YELLOW" "$FM_RESET" "$*" >&2; }
fail() { printf '  %s[x]%s %s\n' "$FM_RED" "$FM_RESET" "$*" >&2; }
die() {
  printf '\n%sОшибка:%s %s\n' "$FM_RED" "$FM_RESET" "$*" >&2
  exit 1
}

# env_get FILE KEY: prints the value of KEY in a dotenv file (last assignment
# wins, surrounding quotes and unquoted " # comments" removed). Prints nothing
# if the file or the key is missing.
env_get() {
  local file=$1 key=$2
  [[ -f $file ]] || return 0
  awk -v k="$key" '
    index($0, k "=") == 1 { v = substr($0, length(k) + 2); found = 1 }
    END {
      if (!found) exit
      sub(/\r$/, "", v)
      if (v ~ /^".*"$/ || v ~ /^\047.*\047$/) {
        v = substr(v, 2, length(v) - 2)
      } else {
        sub(/[ \t]+#.*$/, "", v)
        sub(/[ \t]+$/, "", v)
      }
      printf "%s", v
    }' "$file"
}

# env_has FILE KEY: succeeds if KEY is assigned in the file.
env_has() {
  [[ -f $1 ]] && grep -q "^$2=" "$1"
}

# env_set FILE KEY VALUE: replaces the assignment of KEY (or appends it). The
# value is written unquoted, so only use it for values without spaces or "#".
env_set() {
  local file=$1 key=$2 value=$3 tmp
  tmp=$(mktemp "$file.XXXXXX")
  FM_K=$key FM_V=$value awk '
    BEGIN { k = ENVIRON["FM_K"]; v = ENVIRON["FM_V"] }
    index($0, k "=") == 1 { if (!done) print k "=" v; done = 1; next }
    { print }
    END { if (!done) print k "=" v }' "$file" >"$tmp"
  chmod 600 "$tmp"
  mv -f "$tmp" "$file"
}

# fm_load_hosts FILE: the public names of FusionMeet from a dotenv FILE
# (deploy/.env), with the defaults of compose.yaml: an empty MEET_HOST,
# KEYCLOAK_HOST or LIVEKIT_HOST means meet., id. or livekit.<BASE_DOMAIN>. Sets
# FM_MEET_HOST, FM_REDIRECT_HOSTS (array, MEET_REDIRECT_HOSTS is space separated),
# FM_KEYCLOAK_HOST, FM_LIVEKIT_HOST and FM_HOSTS (all of them, MEET_HOST first).
fm_load_hosts() {
  local file=$1 base
  base=$(env_get "$file" BASE_DOMAIN)
  FM_MEET_HOST=$(env_get "$file" MEET_HOST)
  FM_MEET_HOST=${FM_MEET_HOST:-meet.$base}
  FM_KEYCLOAK_HOST=$(env_get "$file" KEYCLOAK_HOST)
  FM_KEYCLOAK_HOST=${FM_KEYCLOAK_HOST:-id.$base}
  FM_LIVEKIT_HOST=$(env_get "$file" LIVEKIT_HOST)
  FM_LIVEKIT_HOST=${FM_LIVEKIT_HOST:-livekit.$base}
  read -r -a FM_REDIRECT_HOSTS <<<"$(env_get "$file" MEET_REDIRECT_HOSTS)"
  # shellcheck disable=SC2034  # read by the scripts that source lib.sh
  FM_HOSTS=("$FM_MEET_HOST" "${FM_REDIRECT_HOSTS[@]}" "$FM_KEYCLOAK_HOST" "$FM_LIVEKIT_HOST")
}

# fm_check_hosts: dies unless every name of fm_load_hosts is a plain lower-case
# host name (no scheme, path or "$" reference) and no name is used twice (Caddy
# refuses a duplicate site address, nginx would get conflicting server blocks).
fm_check_hosts() {
  # ASCII ranges in the patterns below whatever the locale of the server
  local LC_ALL=C
  local item key host seen=' ' items=("MEET_HOST=$FM_MEET_HOST")
  for host in "${FM_REDIRECT_HOSTS[@]}"; do items+=("MEET_REDIRECT_HOSTS=$host"); done
  items+=("KEYCLOAK_HOST=$FM_KEYCLOAK_HOST" "LIVEKIT_HOST=$FM_LIVEKIT_HOST")
  for item in "${items[@]}"; do
    key=${item%%=*}
    host=${item#*=}
    case $host in
      *'$'*) die "$key=$host в $FM_ENV_FILE: впишите имя целиком, ссылки на другие переменные (\$...) здесь не работают" ;;
      *://* | */*) die "$key=$host в $FM_ENV_FILE: укажите только имя, без https:// и пути (например med-question.online)" ;;
      *,*) die "$key=$host в $FM_ENV_FILE: имена разделяются пробелами, не запятыми" ;;
      *[A-Z]*) die "$key=$host в $FM_ENV_FILE: имя пишется строчными буквами" ;;
    esac
    if ((${#host} > 253)) ||
      [[ ! $host =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]([a-z0-9-]{0,61}[a-z0-9])?$ ]]; then
      die "$key=$host в $FM_ENV_FILE не похоже на доменное имя (например med-question.online; кириллический домен — в punycode, xn--...)"
    fi
    [[ $seen != *" $host "* ]] ||
      die "имя $host указано в $FM_ENV_FILE дважды (MEET_HOST, MEET_REDIRECT_HOSTS, KEYCLOAK_HOST, LIVEKIT_HOST): у каждого адреса своё имя"
    seen+="$host "
  done
}

# fm_resolve HOST: the first IPv4 address of HOST in the server's DNS, if any.
fm_resolve() {
  getent ahostsv4 "$1" 2>/dev/null | awk 'NR == 1 { print $1 }' || true
}

# fm_compose ARGS...: docker compose on deploy/compose.yaml with deploy/.env
fm_compose() {
  docker compose -f "$FM_COMPOSE_FILE" --env-file "$FM_ENV_FILE" "$@"
}

valid_ipv4() {
  [[ $1 =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]] || return 1
  local octet
  for octet in "${BASH_REMATCH[@]:1}"; do
    ((10#$octet <= 255)) || return 1
  done
}

# Services that answer with the IPv4 address the request came from
FM_PUBLIC_IP_URLS=(https://api.ipify.org https://ipv4.icanhazip.com https://ifconfig.me/ip)

# fm_detect_public_ip: prints the current external IPv4 address of the server.
# At least two services must give the same address, so that one wrong or
# unreachable service never makes LiveKit restart with a wrong IP.
fm_detect_public_ip() {
  command -v curl >/dev/null 2>&1 || return 1
  local url ip answers=()
  for url in "${FM_PUBLIC_IP_URLS[@]}"; do
    ip=$(curl -4 -fsS --max-time 5 "$url" 2>/dev/null | tr -d '[:space:]') || continue
    valid_ipv4 "$ip" && answers+=("$ip")
  done
  ((${#answers[@]} > 0)) || return 1
  ip=$(printf '%s\n' "${answers[@]}" | sort | uniq -c | sort -rn |
    awk 'NR == 1 && $1 >= 2 { print $2 }')
  [[ -n $ip ]] || return 1
  printf '%s' "$ip"
}

# fm_lock WAIT: takes the lock shared by up.sh and public-ip.sh, so that they
# never edit deploy/.env or restart services at the same time. WAIT is the number
# of seconds to wait for it (0: fail at once). Without flock there is no lock.
FM_LOCK_FILE=/run/lock/fusionmeet-deploy.lock
fm_lock() {
  command -v flock >/dev/null 2>&1 || return 0
  [[ -d ${FM_LOCK_FILE%/*} ]] || FM_LOCK_FILE=$FM_DEPLOY_DIR/.deploy.lock
  exec 9>"$FM_LOCK_FILE"
  if (($1 > 0)); then flock -w "$1" 9; else flock -n 9; fi
}

# require_docker: Docker and the compose plugin are installed and reachable.
require_docker() {
  command -v docker >/dev/null 2>&1 ||
    die "Docker не установлен. Запустите сначала: sudo $FM_DEPLOY_DIR/install.sh"
  docker info >/dev/null 2>&1 ||
    die "Нет доступа к Docker. Запустите скрипт через sudo (или добавьте пользователя в группу docker)."
  docker compose version >/dev/null 2>&1 ||
    die "Не найден docker compose (плагин docker-compose-plugin). Запустите: sudo $FM_DEPLOY_DIR/install.sh"
}

# kc_admin SCRIPT ARGS...: runs a bash SCRIPT inside the keycloak container with
# kcadm.sh logged in as the bootstrap admin ("$kcadm" and "$cfg" are set; the
# admin password comes from the container env, never from a command line).
kc_admin() {
  local script=$1
  shift
  # shellcheck disable=SC2016  # expanded by bash inside the container
  local prelude='
set -euo pipefail
kcadm=/opt/keycloak/bin/kcadm.sh
cfg=/tmp/kcadm-$$.config
trap "rm -f \"$cfg\"" EXIT
KC_CLI_PASSWORD=$KC_BOOTSTRAP_ADMIN_PASSWORD "$kcadm" config credentials \
  --config "$cfg" --server http://localhost:8080 --realm master \
  --user "$KC_BOOTSTRAP_ADMIN_USERNAME" >/dev/null
'
  fm_compose exec -T keycloak bash -c "$prelude$script" kc-admin "$@"
}
