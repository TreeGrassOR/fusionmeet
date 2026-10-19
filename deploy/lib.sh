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

# fm_compose ARGS...: docker compose on deploy/compose.yaml with deploy/.env
fm_compose() {
  docker compose -f "$FM_COMPOSE_FILE" --env-file "$FM_ENV_FILE" "$@"
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
