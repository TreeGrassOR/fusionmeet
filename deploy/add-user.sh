#!/usr/bin/env bash
# FusionMeet: создать пользователя в Keycloak (realm meet) и напечатать временный
# пароль. При первом входе Keycloak попросит сменить пароль и указать имя и
# фамилию (они видны другим участникам звонка).
#
#   sudo ./deploy/add-user.sh <логин> <email>
#
# Если пользователь уже есть, скрипт задаёт ему новый временный пароль.
set -euo pipefail

# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

usage() {
  echo "Использование: sudo $0 <логин> <email>" >&2
  exit 2
}

[[ $# -eq 2 ]] || usage
# Keycloak stores user names in lower case
login=$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')
email=$2
[[ $login =~ ^[a-z0-9][a-z0-9._-]{0,63}$ ]] ||
  die "логин может содержать только латинские буквы, цифры, точку, дефис и подчёркивание"
[[ $email =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] ||
  die "«$email» не похож на email"

[[ -f $FM_ENV_FILE ]] || die "нет $FM_ENV_FILE: сначала запустите sudo $FM_DEPLOY_DIR/up.sh"
require_docker
command -v openssl >/dev/null 2>&1 || die "нужен openssl"

[[ -n $(fm_compose ps --status running --quiet keycloak 2>/dev/null) ]] ||
  die "контейнер keycloak не запущен: sudo $FM_DEPLOY_DIR/up.sh"

# 16 characters from [A-Za-z0-9]
password=$(openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | cut -c1-16)
[[ ${#password} -eq 16 ]] || die "не удалось сгенерировать пароль"

# Runs inside the keycloak container. Secrets never appear in command lines:
# the new password comes on stdin, kcadm reads passwords from KC_CLI_PASSWORD.
# shellcheck disable=SC2016  # expanded by bash inside the container
kc_script='
set -euo pipefail
login=$1
email=$2
IFS= read -r new_password
kcadm=/opt/keycloak/bin/kcadm.sh
cfg=/tmp/kcadm-$$.config
trap "rm -f \"$cfg\"" EXIT

KC_CLI_PASSWORD=$KC_BOOTSTRAP_ADMIN_PASSWORD "$kcadm" config credentials \
  --config "$cfg" --server http://localhost:8080 --realm master \
  --user "$KC_BOOTSTRAP_ADMIN_USERNAME" >/dev/null

user_id=$("$kcadm" get users --config "$cfg" -r meet \
  -q "username=$login" -q exact=true --fields id --format csv --noquotes)
if [ -n "$user_id" ]; then
  current_email=$("$kcadm" get "users/$user_id" --config "$cfg" -r meet \
    --fields email --format csv --noquotes)
  echo "status=exists"
  echo "email=$current_email"
else
  "$kcadm" create users --config "$cfg" -r meet \
    -s "username=$login" -s "email=$email" -s emailVerified=true -s enabled=true \
    -i >/dev/null
  echo "status=created"
  echo "email=$email"
fi

KC_CLI_PASSWORD=$new_password "$kcadm" set-password --config "$cfg" -r meet \
  --username "$login" --temporary
'

if ! result=$(printf '%s\n' "$password" |
  fm_compose exec -T keycloak bash -c "$kc_script" add-user "$login" "$email"); then
  die "Keycloak вернул ошибку (см. сообщение выше). Например, этот email уже занят другим пользователем."
fi

status=$(printf '%s\n' "$result" | sed -n 's/^status=//p')
user_email=$(printf '%s\n' "$result" | sed -n 's/^email=//p')
base_domain=$(env_get "$FM_ENV_FILE" BASE_DOMAIN)

echo
if [[ $status == exists ]]; then
  echo "Пользователь $login уже существует (email: ${user_email:-не указан}); ему задан новый временный пароль."
else
  echo "Пользователь $login создан."
fi
cat <<EOF

  Адрес:             https://meet.$base_domain/
  Логин:             $login
  Email:             ${user_email:-$email}
  Временный пароль:  $password

При первом входе Keycloak попросит сменить пароль и указать имя и фамилию.
EOF
