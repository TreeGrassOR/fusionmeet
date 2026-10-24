#!/usr/bin/env bash
# FusionMeet: держать PUBLIC_IP в deploy/.env равным текущему внешнему IP сервера
# и перезапускать LiveKit, когда IP сменился (LiveKit сообщает этот адрес
# участникам для аудио и видео).
#
#   sudo ./deploy/public-ip.sh
#
# Запускается таймером systemd fusionmeet-public-ip.timer каждые 5 минут (его
# ставит up.sh при PUBLIC_IP_AUTO=true). Ничего не делает при PUBLIC_IP_AUTO,
# отличном от true, и пока работает up.sh. A-записи домена не меняет.
set -euo pipefail

# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

((EUID == 0)) || die "запустите через sudo: sudo $0"
[[ -f $FM_ENV_FILE ]] || exit 0
[[ $(env_get "$FM_ENV_FILE" PUBLIC_IP_AUTO) == true ]] || exit 0
# up.sh holds the lock: it determines PUBLIC_IP itself
fm_lock 0 || exit 0

if ! new_ip=$(fm_detect_public_ip); then
  warn "не удалось узнать внешний IP (${FM_PUBLIC_IP_URLS[*]}), PUBLIC_IP не изменён"
  exit 0
fi
old_ip=$(env_get "$FM_ENV_FILE" PUBLIC_IP)
[[ $new_ip != "$old_ip" ]] || exit 0

env_set "$FM_ENV_FILE" PUBLIC_IP "$new_ip"
ok "внешний IP сменился: $old_ip -> $new_ip (PUBLIC_IP в $FM_ENV_FILE)"

# LiveKit gets PUBLIC_IP from its config (compose.yaml): recreate it if it runs
if [[ -n $(fm_compose ps --quiet --status running livekit 2>/dev/null || true) ]]; then
  if fm_compose up --detach --no-deps livekit >/dev/null 2>&1; then
    ok "LiveKit перезапущен с новым адресом (идущие звонки переподключаются сами)"
  else
    fail "не удалось перезапустить LiveKit: sudo docker compose -f $FM_COMPOSE_FILE up -d livekit"
    exit 1
  fi
fi

fm_load_hosts "$FM_ENV_FILE"
for host in "$FM_MEET_HOST" "$FM_KEYCLOAK_HOST" "$FM_LIVEKIT_HOST"; do
  resolved=$(getent ahostsv4 "$host" 2>/dev/null | awk 'NR == 1 { print $1 }' || true)
  if [[ $resolved != "$new_ip" ]]; then
    warn "$host указывает на ${resolved:-ничего}: поменяйте A-запись у регистратора на $new_ip"
  fi
done
