# shellcheck shell=bash
# FusionMeet behind an nginx of the host (PROXY_MODE=nginx, see .env.example).
# Sourced by up.sh after lib.sh, not executed.
#
# The host nginx keeps ports 80/443 for its own sites and forwards only the
# FusionMeet names (MEET_HOST, MEET_REDIRECT_HOSTS, KEYCLOAK_HOST, LIVEKIT_HOST)
# to Caddy on 127.0.0.1:
#   - port 80 -> Caddy HTTP (Let's Encrypt HTTP-01 challenges, redirect to https);
#   - port 443 -> Caddy HTTPS, with the certificates Caddy obtained (read from
#     its data volume, so there is a single ACME client: Caddy).
# The containers keep reaching the names through Caddy's network aliases.

HN_CONF_NAME=fusionmeet.conf
HN_RELOAD_UNIT=fusionmeet-nginx-reload
# Set by hn_resolve_mode, read by up.sh
export HN_MODE=""

# hn_owner PORT: prints "nginx" when the host nginx listens on PORT/tcp,
# "docker" when it is a published container port, the process name otherwise,
# nothing when the port is free.
hn_owner() {
  local procs
  procs=$(ss -Hltnp "sport = :$1" 2>/dev/null |
    grep -o 'users:(("[^"]*"' | cut -d'"' -f2 | sort -u || true)
  [[ -n $procs ]] || return 0
  if grep -qx nginx <<<"$procs"; then
    echo nginx
  elif grep -qx docker-proxy <<<"$procs"; then
    echo docker
  else
    head -n 1 <<<"$procs"
  fi
}

# hn_resolve_mode: decides between "caddy" (Caddy publishes 80/443) and
# "nginx" (behind the host nginx), and writes the Caddy port bindings to
# deploy/.env so that later "docker compose" commands use the same ones.
hn_resolve_mode() {
  local mode http_port https_port owner80 owner443
  mode=$(env_get "$FM_ENV_FILE" PROXY_MODE)
  mode=${mode:-auto}
  http_port=$(env_get "$FM_ENV_FILE" CADDY_LOCAL_HTTP_PORT)
  https_port=$(env_get "$FM_ENV_FILE" CADDY_LOCAL_HTTPS_PORT)
  http_port=${http_port:-18080}
  https_port=${https_port:-18443}

  if [[ $mode == auto ]]; then
    owner80=$(hn_owner 80)
    owner443=$(hn_owner 443)
    if [[ $owner80 == nginx || $owner443 == nginx ]]; then
      mode=nginx
      # Pinned: a stopped nginx on a later run must not switch Caddy to 80/443
      env_set "$FM_ENV_FILE" PROXY_MODE nginx
      note "порты 80/443 заняты nginx сервера: PROXY_MODE=nginx записан в $FM_ENV_FILE"
    else
      mode=caddy
      for owner in "$owner80" "$owner443"; do
        if [[ -n $owner && $owner != docker ]]; then
          die "порт 80 или 443 занят процессом $owner. FusionMeet умеет вставать только
за nginx сервера (PROXY_MODE=nginx) или сам слушать 80/443 (PROXY_MODE=caddy)."
        fi
      done
    fi
  fi

  case $mode in
    caddy)
      env_set "$FM_ENV_FILE" CADDY_HTTP_BIND 80
      env_set "$FM_ENV_FILE" CADDY_HTTPS_BIND 443
      env_set "$FM_ENV_FILE" CADDY_HTTP3_BIND 443
      ok "обратный прокси: Caddy слушает 80/443 сам"
      ;;
    nginx)
      command -v nginx >/dev/null 2>&1 || die "PROXY_MODE=nginx, но nginx на сервере не найден"
      env_set "$FM_ENV_FILE" CADDY_HTTP_BIND "127.0.0.1:$http_port"
      env_set "$FM_ENV_FILE" CADDY_HTTPS_BIND "127.0.0.1:$https_port"
      env_set "$FM_ENV_FILE" CADDY_HTTP3_BIND "127.0.0.1:$https_port"
      ok "обратный прокси: nginx сервера (порты 80/443) -> Caddy на 127.0.0.1:$http_port и :$https_port"
      ;;
    *) die "PROXY_MODE=$mode в $FM_ENV_FILE: допустимо auto, caddy или nginx" ;;
  esac
  HN_MODE=$mode
  HN_HTTP_PORT=$http_port
  HN_HTTPS_PORT=$https_port
}

# hn_conf_path: where the generated server blocks go (conf.d if the host nginx
# includes it, otherwise sites-available + a sites-enabled link).
hn_conf_path() {
  local dump
  dump=$(nginx -T 2>/dev/null || true)
  if grep -q 'include /etc/nginx/conf.d/\*.conf' <<<"$dump"; then
    echo "/etc/nginx/conf.d/$HN_CONF_NAME"
  elif grep -q 'include /etc/nginx/sites-enabled/\*' <<<"$dump"; then
    echo "/etc/nginx/sites-available/$HN_CONF_NAME"
  else
    die "nginx сервера не подключает ни /etc/nginx/conf.d/*.conf, ни /etc/nginx/sites-enabled/*:
не знаю, куда положить настройки FusionMeet. Добавьте в блок http {} строку
include /etc/nginx/conf.d/*.conf; и запустите up.sh снова."
  fi
}

# hn_cert_dir: Caddy's certificate storage on the host (inside its data volume).
hn_cert_dir() {
  local mountpoint
  mountpoint=$(docker volume inspect -f '{{.Mountpoint}}' "${FM_PROJECT}_caddy-data" 2>/dev/null || true)
  [[ -n $mountpoint ]] && echo "$mountpoint/caddy/certificates"
}

# hn_cert HOST EXT: prints the path of the newest certificate (crt) or key of HOST.
hn_cert() {
  local dir
  dir=$(hn_cert_dir)
  [[ -n $dir && -d $dir ]] || return 0
  find "$dir" -path "*/$1/$1.$2" -type f -printf '%T@ %p\n' 2>/dev/null |
    sort -rn | awk 'NR == 1 { print $2 }'
}

hn_have_cert() {
  [[ -n $(hn_cert "$1" crt) && -n $(hn_cert "$1" key) ]]
}

hn_have_certs() {
  local host
  for host in "$@"; do
    hn_have_cert "$host" || return 1
  done
}

# hn_check_names HOSTS...: dies when another site of the host nginx already uses
# one of the names. nginx only warns about a "conflicting server name" and keeps
# the first block it read, so FusionMeet could silently lose the name (and its
# Let's Encrypt challenges).
hn_check_names() {
  local file name conflicts=() wanted=" $* "
  while IFS=$'\t' read -r file name; do
    if [[ $wanted == *" $name "* ]]; then conflicts+=("$name ($file)"); fi
  done < <(nginx -T 2>/dev/null | awk -v ours="$HN_CONF_NAME" '
    /^# configuration file .*:$/ {
      file = $4; sub(/:$/, "", file); n = split(file, parts, "/")
      skip = (parts[n] == ours); collect = 0; next
    }
    skip { next }
    {
      sub(/#.*/, ""); gsub(/[;{}]/, " & ")
      for (i = 1; i <= NF; i++) {
        if ($i == "server_name") { collect = 1 }
        else if ($i == ";" || $i == "{" || $i == "}") { collect = 0 }
        else if (collect) { name = tolower($i); gsub(/"|\047/, "", name); print file "\t" name }
      }
    }')
  ((${#conflicts[@]} > 0)) || return 0
  die "в nginx сервера эти имена уже заняты другими сайтами:
$(printf '  %s\n' "${conflicts[@]}")
Уберите их из server_name этих файлов (или отключите эти сайты), проверьте nginx -t
и запустите up.sh снова: эти имена FusionMeet обслуживает сам."
}

# hn_render HOSTS...: the nginx configuration of the FusionMeet names: HTTP for
# all of them, HTTPS for those whose certificate Caddy already has.
hn_render() {
  local host
  cat <<EOF
# FusionMeet: generated by deploy/up.sh (PROXY_MODE=nginx). Do not edit, it is
# rewritten on every run of up.sh. Only the names below are forwarded to
# FusionMeet; the other sites of this nginx are not affected.

map \$http_upgrade \$fusionmeet_connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen 80;
    server_name $*;

    # Let's Encrypt HTTP-01 challenges and the redirect to https are Caddy's
    location / {
        proxy_pass http://127.0.0.1:$HN_HTTP_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    }
}
EOF
  for host in "$@"; do
    hn_have_cert "$host" || continue
    cat <<EOF

server {
    listen 443 ssl;
    server_name $host;

    # Certificate obtained and renewed by Caddy; nginx re-reads it on reload
    # (systemd timer $HN_RELOAD_UNIT.timer, daily)
    ssl_certificate $(hn_cert "$host" crt);
    ssl_certificate_key $(hn_cert "$host" key);

    # Uploads, recording downloads and the LiveKit WebSocket: no size limit,
    # no buffering, long-lived connections
    client_max_body_size 0;

    location / {
        proxy_pass https://127.0.0.1:$HN_HTTPS_PORT;
        proxy_ssl_server_name on;
        proxy_ssl_name \$host;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$fusionmeet_connection_upgrade;
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_read_timeout 1h;
        proxy_send_timeout 1h;
        # HTTP/3 of Caddy is not reachable through this nginx
        proxy_hide_header Alt-Svc;
    }
}
EOF
  done
}

# hn_apply HOSTS...: writes the configuration, checks it with nginx -t and
# reloads nginx. A configuration that fails nginx -t is removed again, so the
# other sites of the host nginx keep working.
hn_apply() {
  local path backup="" tmp host https=() http_only=()
  path=$(hn_conf_path)
  tmp=$(mktemp)
  hn_render "$@" >"$tmp"
  if [[ -f $path ]]; then
    if cmp -s "$tmp" "$path"; then
      rm -f "$tmp"
      return 0
    fi
    backup=$(mktemp)
    cp "$path" "$backup"
  fi
  install -m 0644 "$tmp" "$path"
  rm -f "$tmp"
  if [[ $path == /etc/nginx/sites-available/* ]]; then
    ln -sf "$path" "/etc/nginx/sites-enabled/$HN_CONF_NAME"
  fi
  local check
  if ! check=$(nginx -t 2>&1); then
    if [[ -n $backup ]]; then mv -f "$backup" "$path"; else rm -f "$path" "/etc/nginx/sites-enabled/$HN_CONF_NAME"; fi
    die "nginx -t не принял настройки FusionMeet, прежние настройки nginx восстановлены:
$check"
  fi
  [[ -z $backup ]] || rm -f "$backup"
  systemctl reload nginx
  for host in "$@"; do
    if hn_have_cert "$host"; then https+=("$host"); else http_only+=("$host"); fi
  done
  ok "nginx сервера пересылает в FusionMeet: $path"
  if ((${#https[@]} > 0)); then note "HTTP и HTTPS: ${https[*]}"; fi
  if ((${#http_only[@]} > 0)); then
    note "пока только HTTP, до выпуска сертификата: ${http_only[*]}"
  fi
}

# hn_install_reload_timer: daily "nginx -t && reload", so nginx picks up the
# certificates Caddy renews (about a month before they expire).
hn_install_reload_timer() {
  local unit=/etc/systemd/system/$HN_RELOAD_UNIT
  cat >"$unit.service" <<EOF
[Unit]
Description=FusionMeet: reload nginx to pick up certificates renewed by Caddy

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'nginx -t -q && systemctl reload nginx'
EOF
  cat >"$unit.timer" <<EOF
[Unit]
Description=FusionMeet: daily nginx reload for renewed certificates

[Timer]
OnCalendar=daily
RandomizedDelaySec=1h
Persistent=true

[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable --now "$HN_RELOAD_UNIT.timer" >/dev/null 2>&1
  ok "nginx будет перечитывать обновлённые сертификаты раз в сутки ($HN_RELOAD_UNIT.timer)"
}

# hn_before_up HOSTS...: HTTP forwarding of every name, and HTTPS for the names
# that already have a certificate (so they keep working while the new ones get
# theirs). Called right before "up", not before the image build: until "up"
# replaces them, the running containers still use the names of the previous
# run, which the new configuration may no longer forward.
hn_before_up() {
  hn_apply "$@"
}

# hn_wait_certs SECONDS HOSTS...: waits up to SECONDS until every name of HOSTS
# has its certificate; fails if one is still missing then.
hn_wait_certs() {
  local limit=$1 waited=0
  shift
  until hn_have_certs "$@"; do
    ((waited < limit)) || return 1
    sleep 5
    waited=$((waited + 5))
  done
}

# hn_after_up "MAIN..." HOSTS...: waits for Caddy's certificates, then enables
# HTTPS for every name of HOSTS that has one. The MAIN names (space separated:
# the site, Keycloak, LiveKit) get HN_CERT_TIMEOUT seconds; one still without a
# certificate stops up.sh, after HTTPS of the other names is enabled. The other
# names (MEET_REDIRECT_HOSTS) only get a warning, and at most 30 s more: Caddy
# asks for all the certificates at once.
hn_after_up() {
  local main=() host waiting=() missing=() late=() limit=${HN_CERT_TIMEOUT:-300} grace upto
  read -r -a main <<<"$1"
  shift
  grace=$((limit < 30 ? limit : 30))
  for host in "$@"; do hn_have_cert "$host" || waiting+=("$host"); done
  if ((${#waiting[@]} > 0)); then
    upto=$limit
    if hn_have_certs "${main[@]}"; then upto=$grace; fi
    note "жду сертификаты Let's Encrypt для ${waiting[*]} (до $upto с) ..."
    if hn_wait_certs "$limit" "${main[@]}"; then
      hn_wait_certs "$grace" "$@" || true
    fi
  fi
  hn_apply "$@"
  hn_install_reload_timer
  for host in "$@"; do
    hn_have_cert "$host" && continue
    if [[ " ${main[*]} " == *" $host "* ]]; then missing+=("$host"); else late+=("$host"); fi
  done
  if ((${#late[@]} > 0)); then
    warn "нет сертификата для ${late[*]} (MEET_REDIRECT_HOSTS): пока только HTTP. Проверьте A-записи и журнал caddy, затем запустите up.sh снова"
  fi
  ((${#missing[@]} == 0)) ||
    die "Caddy не получил за $limit с сертификаты Let's Encrypt для ${missing[*]}.
Для имён, у которых сертификат есть, HTTPS уже включён. Проверьте:
  - A-записи этих имён ведут на PUBLIC_IP из $FM_ENV_FILE (getent hosts <имя>;
    только что добавленную запись Let's Encrypt может не видеть до 30 минут);
  - порт 80 проброшен на роутере на этот сервер;
  - журнал: sudo docker compose -f $FM_COMPOSE_FILE logs caddy | grep -i -E 'error|acme'
Затем запустите up.sh снова."
}
