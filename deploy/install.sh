#!/usr/bin/env bash
# FusionMeet: подготовка сервера (Ubuntu или Debian) перед deploy/up.sh.
#
#   sudo ./deploy/install.sh
#
# Что делает (повторный запуск безопасен):
#   1. проверяет драйвер NVIDIA (nvidia-smi);
#   2. ставит Docker Engine, docker compose и docker buildx из официального
#      репозитория Docker, если их нет;
#   3. ставит NVIDIA Container Toolkit из официального репозитория NVIDIA, если его
#      нет, и регистрирует runtime nvidia в Docker;
#   4. проверяет, что контейнер видит GPU;
#   5. проверяет, что нужные порты свободны;
#   6. показывает свободную видеопамять и сравнивает её с ASR_MAX_VRAM_GB.
# Существующие контейнеры (например, vLLM) скрипт не трогает: он не
# останавливает и не перенастраивает их и не перезапускает Docker, пока они
# работают, без live-restore и вашего согласия.
set -euo pipefail

# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# Pinned versions
NVIDIA_CONTAINER_TOOLKIT_VERSION=1.20.1-1
DOCKER_MAJOR=29                                   # newest 29.x of the Docker repo
GPU_TEST_IMAGE=nvidia/cuda:12.8.2-base-ubuntu24.04 # base image of asr/Dockerfile
# asr runs the torch cu128 wheels (CUDA 12.8 runtime bundled). CUDA minor version
# compatibility runs any CUDA 12.x build on driver >= 525.60.13 (CUDA 12.0 GA);
# CUDA 12.8 itself ships with R570, which needs no compatibility mode.
MIN_DRIVER_VERSION=525.60.13
CUDA128_DRIVER_VERSION=570
MIN_COMPOSE_VERSION=2.24.0
MIN_BUILDX_VERSION=0.17.0                         # required by "docker compose build"
MIN_DISK_GB=30

PROBLEMS=()  # blocking issues
TODO=()      # what is left for the user
WARNINGS=()  # not blocking, repeated in the summary

[[ $EUID -eq 0 ]] || die "запустите через sudo: sudo $0"
# shellcheck disable=SC1091
[[ -r /etc/os-release ]] && source /etc/os-release
case ${ID:-} in
  ubuntu | debian) ;;
  *) die "скрипт рассчитан на Ubuntu или Debian (обнаружено: ${PRETTY_NAME:-неизвестно})" ;;
esac
command -v apt-get >/dev/null 2>&1 || die "не найден apt-get"

apt_install() {
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$@"
}

# version_ge A B: A >= B for dotted versions ("v" prefix allowed)
version_ge() {
  local a=${1#v} b=${2#v}
  [[ $(printf '%s\n%s\n' "$b" "$a" | sort -V | head -n 1) == "$b" ]]
}

ask_yes() {
  local answer
  [[ -t 0 ]] || return 1
  read -r -p "  $1 [y/N] " answer
  [[ $answer == [yYдД]* ]]
}

# ---------------------------------------------------------------------------
check_nvidia_driver() {
  step "1. Драйвер NVIDIA"
  if ! command -v nvidia-smi >/dev/null 2>&1 || ! nvidia-smi -L >/dev/null 2>&1; then
    fail "nvidia-smi не работает: драйвер NVIDIA не установлен или не загружен."
    note "Установите драйвер (например: sudo ubuntu-drivers install), перезагрузите сервер"
    note "и запустите install.sh снова."
    exit 1
  fi
  local driver
  driver=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -n 1)
  nvidia-smi -L | while IFS= read -r line; do ok "$line"; done
  if ! version_ge "$driver" "$MIN_DRIVER_VERSION"; then
    PROBLEMS+=("драйвер NVIDIA $driver ниже $MIN_DRIVER_VERSION: asr (CUDA 12.8) на нём не запустится")
    fail "драйвер $driver: нужен $MIN_DRIVER_VERSION или новее (CUDA 12.x), рекомендуется $CUDA128_DRIVER_VERSION+"
  elif ! version_ge "$driver" "$CUDA128_DRIVER_VERSION"; then
    WARNINGS+=("драйвер NVIDIA $driver ниже $CUDA128_DRIVER_VERSION: asr полагается на совместимость минорных версий CUDA и на таком драйвере не проверялся")
    warn "драйвер $driver ниже $CUDA128_DRIVER_VERSION (CUDA 12.8)"
    note "asr (torch с CUDA 12.8) должен работать через совместимость минорных версий CUDA"
    note "(CUDA minor version compatibility, драйвер $MIN_DRIVER_VERSION+), но на драйверах"
    note "ниже $CUDA128_DRIVER_VERSION не проверялся. Обновлять драйвер ради asr не обязательно"
    note "(обновление остановит vLLM). Если asr не запустится, смотрите журнал asr и"
    note "раздел «Если что-то не работает» в $FM_DEPLOY_DIR/README.md."
  else
    ok "драйвер $driver"
  fi
}

# ---------------------------------------------------------------------------
install_docker() {
  local codename version
  codename=${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}
  [[ -n $codename ]] || die "не удалось определить кодовое имя дистрибутива"
  apt-get update
  apt_install ca-certificates curl
  if [[ ! -f /etc/apt/sources.list.d/docker.list && ! -f /etc/apt/sources.list.d/docker.sources ]]; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$ID $codename stable" \
      >/etc/apt/sources.list.d/docker.list
    apt-get update
  fi
  # No early "exit" in awk: apt-cache would get SIGPIPE, and with pipefail the
  # assignment would silently end the script.
  version=$(apt-cache madison docker-ce | awk -v m="5:$DOCKER_MAJOR." 'index($3, m) == 1 && !v { v = $3 } END { print v }')
  [[ -n $version ]] || die "в репозитории Docker нет версии $DOCKER_MAJOR.x для $ID $codename"
  apt_install "docker-ce=$version" "docker-ce-cli=$version" containerd.io \
    docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
}

ensure_docker() {
  step "2. Docker"
  if ! command -v docker >/dev/null 2>&1; then
    note "Docker не найден, устанавливаю из https://download.docker.com ..."
    install_docker
  fi
  if ! docker info >/dev/null 2>&1; then
    if [[ -z $(pgrep -x dockerd || true) ]]; then
      note "dockerd не запущен, запускаю (контейнеров без dockerd нет, прерывать нечего)"
      systemctl enable --now docker
    fi
    docker info >/dev/null 2>&1 || die "Docker установлен, но не отвечает (systemctl status docker)"
  fi
  if ! docker compose version >/dev/null 2>&1; then
    note "нет плагина docker compose, устанавливаю ..."
    apt-get update
    apt_install docker-compose-plugin || apt_install docker-compose-v2 ||
      die "не удалось установить docker compose"
  fi
  # docker compose builds images through buildx (bake); without it, it falls
  # back to the classic builder, which rejects the RUN --mount of our Dockerfiles
  if ! docker buildx version >/dev/null 2>&1; then
    note "нет плагина docker buildx (нужен для сборки образов), устанавливаю ..."
    apt-get update
    apt_install docker-buildx-plugin || apt_install docker-buildx ||
      die "не удалось установить docker buildx"
  fi
  local engine compose buildx
  engine=$(docker version --format '{{.Server.Version}}')
  compose=$(docker compose version --short)
  # "github.com/docker/buildx v0.29.1 <commit>" -> v0.29.1
  buildx=$(docker buildx version | awk 'NR == 1 { print $2 }')
  ok "Docker Engine $engine"
  ok "Docker Compose $compose"
  ok "Docker Buildx $buildx"
  version_ge "$engine" 20.10.0 || PROBLEMS+=("Docker Engine $engine слишком старый (нужен 20.10+)")
  version_ge "$compose" "$MIN_COMPOSE_VERSION" ||
    PROBLEMS+=("Docker Compose $compose слишком старый (нужен $MIN_COMPOSE_VERSION+): sudo apt-get install docker-compose-plugin")
  version_ge "$buildx" "$MIN_BUILDX_VERSION" ||
    PROBLEMS+=("Docker Buildx $buildx слишком старый (docker compose build требует $MIN_BUILDX_VERSION+): sudo apt-get install docker-buildx-plugin")
}

# ---------------------------------------------------------------------------
toolkit_installed() {
  dpkg-query -W -f='${Status}' nvidia-container-toolkit 2>/dev/null | grep -q 'install ok installed'
}

install_toolkit() {
  local v=$NVIDIA_CONTAINER_TOOLKIT_VERSION
  apt-get update
  apt_install ca-certificates curl gnupg2
  if [[ ! -f /etc/apt/sources.list.d/nvidia-container-toolkit.list ]]; then
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey |
      gpg --dearmor --yes -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list |
      sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
        >/etc/apt/sources.list.d/nvidia-container-toolkit.list
    apt-get update
  fi
  apt_install "nvidia-container-toolkit=$v" "nvidia-container-toolkit-base=$v" \
    "libnvidia-container-tools=$v" "libnvidia-container1=$v"
}

docker_runtimes() {
  docker info --format '{{range $name, $_ := .Runtimes}}{{$name}} {{end}}' 2>/dev/null
}

ensure_toolkit() {
  step "3. NVIDIA Container Toolkit"
  if toolkit_installed; then
    ok "установлен: $(dpkg-query -W -f='${Version}' nvidia-container-toolkit)"
  else
    note "не найден, устанавливаю версию $NVIDIA_CONTAINER_TOOLKIT_VERSION из https://nvidia.github.io/libnvidia-container ..."
    install_toolkit
    ok "установлен: $(dpkg-query -W -f='${Version}' nvidia-container-toolkit)"
  fi

  if [[ " $(docker_runtimes) " == *" nvidia "* ]]; then
    ok "runtime nvidia зарегистрирован в Docker"
    return
  fi
  if ! grep -qs '"nvidia"' /etc/docker/daemon.json; then
    if [[ -f /etc/docker/daemon.json ]]; then
      cp -p /etc/docker/daemon.json "/etc/docker/daemon.json.bak.$(date +%Y%m%d%H%M%S)"
      note "резервная копия /etc/docker/daemon.json сохранена рядом"
    fi
    nvidia-ctk runtime configure --runtime=docker
  fi
  # "runtimes" is reloadable (SIGHUP): running containers are not affected
  systemctl reload docker
  sleep 2
  if [[ " $(docker_runtimes) " == *" nvidia "* ]]; then
    ok "runtime nvidia добавлен в /etc/docker/daemon.json (dockerd перечитал настройки без перезапуска)"
  else
    PROBLEMS+=("Docker не видит runtime nvidia после systemctl reload docker")
    fail "Docker не видит runtime nvidia (проверьте /etc/docker/daemon.json)"
  fi
}

# ---------------------------------------------------------------------------
GPU_TEST_ERROR=''
gpu_test() {
  # NVIDIA_DISABLE_REQUIRE as in asr/Dockerfile: the image's NVIDIA_REQUIRE_CUDA
  # (cuda>=12.8) would reject drivers below 570 on GeForce cards
  GPU_TEST_ERROR=$(docker run --rm --gpus all --env NVIDIA_DISABLE_REQUIRE=true \
    "$GPU_TEST_IMAGE" nvidia-smi -L 2>&1 >/dev/null) && return 0
  [[ -n $GPU_TEST_ERROR ]] || GPU_TEST_ERROR='nvidia-smi завершился с ошибкой'
  return 1
}

enable_live_restore() {
  command -v python3 >/dev/null 2>&1 || return 1
  local file=/etc/docker/daemon.json
  [[ -f $file ]] && cp -p "$file" "$file.bak.$(date +%Y%m%d%H%M%S)"
  python3 - "$file" <<'PY'
import json, os, sys
path = sys.argv[1]
data = {}
if os.path.exists(path) and os.path.getsize(path) > 0:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
data["live-restore"] = True
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=4)
    fh.write("\n")
os.replace(tmp, path)
PY
  # "live-restore" is reloadable (SIGHUP)
  systemctl reload docker
  sleep 2
  [[ $(docker info --format '{{.LiveRestoreEnabled}}') == true ]]
}

restart_docker() {
  local before after missing
  before=$(docker ps -q | sort)
  systemctl restart docker
  for _ in $(seq 1 30); do
    docker info >/dev/null 2>&1 && break
    sleep 1
  done
  after=$(docker ps -q | sort)
  missing=$(comm -23 <(printf '%s\n' "$before") <(printf '%s\n' "$after") | grep -c . || true)
  if ((missing > 0)); then
    warn "после перезапуска Docker не работают контейнеры: $missing (проверьте docker ps -a)"
  else
    ok "dockerd перезапущен, все работавшие контейнеры продолжают работать"
  fi
}

check_gpu_in_docker() {
  step "4. GPU внутри контейнеров"
  if ! docker image inspect "$GPU_TEST_IMAGE" >/dev/null 2>&1 &&
    ! docker pull --quiet "$GPU_TEST_IMAGE" >/dev/null; then
    PROBLEMS+=("не удалось скачать $GPU_TEST_IMAGE для проверки GPU (нет доступа к Docker Hub?)")
    fail "не удалось скачать тестовый образ $GPU_TEST_IMAGE"
    return
  fi
  if gpu_test; then
    ok "контейнер видит GPU ($GPU_TEST_IMAGE, nvidia-smi)"
    return
  fi
  # Docker < 29 says "could not select device driver"; Docker 29 falls back to
  # its AMD CDI driver and says "AMD CDI spec not found".
  if [[ $GPU_TEST_ERROR != *"could not select device driver"* &&
    $GPU_TEST_ERROR != *"AMD CDI spec not found"* ]]; then
    PROBLEMS+=("контейнер не видит GPU: $GPU_TEST_ERROR")
    fail "контейнер не видит GPU: $GPU_TEST_ERROR"
    return
  fi
  # Docker registers its "nvidia" device driver (used by --gpus and by compose
  # GPU reservations) only when dockerd starts, so a toolkit installed after
  # dockerd needs a dockerd restart.
  warn "контейнер не видит GPU: dockerd запущен раньше, чем установлен NVIDIA Container Toolkit"
  local running live_restore
  running=$(docker ps -q | grep -c . || true)
  live_restore=$(docker info --format '{{.LiveRestoreEnabled}}')

  if ((running == 0)); then
    note "запущенных контейнеров нет, перезапускаю dockerd"
    restart_docker
  else
    note "работает контейнеров: $running. Перезапускать dockerd можно только с live-restore,"
    note "тогда контейнеры продолжают работать."
    if [[ $live_restore != true ]]; then
      if [[ $(docker info --format '{{.Swarm.LocalNodeState}}') == active ]]; then
        PROBLEMS+=("GPU недоступен в Docker; live-restore несовместим со swarm: перезапустите dockerd в удобное время")
        return
      fi
      note "включаю live-restore в /etc/docker/daemon.json (systemctl reload docker, без перезапуска)"
      if enable_live_restore; then
        ok "live-restore включён"
        live_restore=true
      else
        warn "не удалось включить live-restore"
      fi
    fi
    if [[ $live_restore == true ]] && ask_yes "Перезапустить dockerd сейчас? Контейнеры не остановятся (live-restore), но их порты могут быть недоступны несколько секунд."; then
      restart_docker
    else
      TODO+=("перезапустить Docker, чтобы он увидел GPU: sudo systemctl restart docker (live-restore: $live_restore), затем снова sudo $0")
      PROBLEMS+=("контейнеры пока не видят GPU")
      return
    fi
  fi

  if gpu_test; then
    ok "контейнер видит GPU"
  else
    PROBLEMS+=("контейнер не видит GPU даже после перезапуска Docker: $GPU_TEST_ERROR")
    fail "контейнер всё ещё не видит GPU: $GPU_TEST_ERROR"
  fi
}

# ---------------------------------------------------------------------------
# port_check PORT PROTO: prints who listens on the port (if anyone) and records
# a problem unless it is this very stack (fusionmeet-* containers).
port_check() {
  local port=$1 proto=$2 flags names procs proc name others=() ours=false behind_nginx=false
  [[ $proto == tcp ]] && flags=-Hltnp || flags=-Hlunp

  names=$(docker ps --filter "publish=$port/$proto" --format '{{.Names}}' 2>/dev/null || true)
  for name in $names; do
    if [[ $name == "$FM_PROJECT"-* ]]; then ours=true; else others+=("контейнер $name"); fi
  done

  procs=$(ss "$flags" "sport = :$port" 2>/dev/null |
    grep -o 'users:(("[^"]*"' | cut -d'"' -f2 | sort -u || true)
  for proc in $procs; do
    case $proc in
      docker-proxy) ;; # covered by "docker ps --filter publish" above
      livekit-server)
        if [[ -n $(docker ps -q --filter "name=^${FM_PROJECT}-livekit-" 2>/dev/null) ]]; then
          ours=true
        else
          others+=("процесс $proc")
        fi
        ;;
      nginx)
        # PROXY_MODE=auto/nginx: up.sh puts FusionMeet behind the host nginx
        if [[ $port == 80 || $port == 443 ]] && [[ $proto == tcp ]] &&
          [[ $(env_get "$FM_ENV_FILE" PROXY_MODE) != caddy ]]; then
          behind_nginx=true
        else
          others+=("процесс $proc")
        fi
        ;;
      *) others+=("процесс $proc") ;;
    esac
  done

  if ((${#others[@]} > 0)); then
    fail "$port/$proto занят: ${others[*]}"
    PROBLEMS+=("порт $port/$proto занят (${others[*]})")
  elif $behind_nginx; then
    ok "$port/$proto занят nginx сервера: FusionMeet встанет за него (up.sh добавит в nginx"
    note "только meet./id./livekit.<BASE_DOMAIN>, остальные сайты не меняются; PROXY_MODE в .env)"
  elif $ours; then
    ok "$port/$proto занят самим FusionMeet (повторный запуск)"
  else
    ok "$port/$proto свободен"
  fi
}

check_ports() {
  step "5. Порты"
  command -v ss >/dev/null 2>&1 || apt_install iproute2
  local redis_port
  redis_port=$(env_get "$FM_ENV_FILE" REDIS_HOST_PORT)
  [[ -n $redis_port ]] || redis_port=$(env_get "$FM_ENV_EXAMPLE" REDIS_HOST_PORT)
  redis_port=${redis_port:-16379}
  port_check 80 tcp
  port_check 443 tcp
  port_check 443 udp
  port_check 7880 tcp
  port_check 7881 tcp
  port_check 7882 udp
  port_check "$redis_port" tcp
  # Caddy's 127.0.0.1 ports when FusionMeet sits behind the host nginx
  local caddy_port key
  for key in CADDY_LOCAL_HTTP_PORT CADDY_LOCAL_HTTPS_PORT; do
    caddy_port=$(env_get "$FM_ENV_FILE" "$key")
    [[ -n $caddy_port ]] || caddy_port=$(env_get "$FM_ENV_EXAMPLE" "$key")
    [[ -z $caddy_port ]] || port_check "$caddy_port" tcp
  done
  note "80/tcp, 443/tcp, 443/udp, 7881/tcp, 7882/udp должны быть проброшены на роутере на этот сервер;"
  note "7880/tcp (API LiveKit) и $redis_port/tcp (Redis, только 127.0.0.1) пробрасывать не нужно."
}

# ---------------------------------------------------------------------------
# Value of --gpu-memory-utilization of a running vLLM (container or process), if any
vllm_utilization() {
  local id args
  for id in $(docker ps -q 2>/dev/null); do
    args=$(docker inspect --format '{{.Name}} {{.Config.Image}} {{join .Config.Entrypoint " "}} {{join .Config.Cmd " "}}' "$id" 2>/dev/null || true)
    if [[ ${args,,} == *vllm* ]]; then
      args=${args//=/ }
      if [[ $args =~ --gpu-memory-utilization[[:space:]]+([0-9.]+) ]]; then
        printf '%s %s\n' "${BASH_REMATCH[1]}" "контейнер ${args%% *}"
        return
      fi
      printf '%s %s\n' "0.9" "контейнер ${args%% *} (значение по умолчанию vLLM)"
      return
    fi
  done
  args=$(pgrep -af vllm 2>/dev/null | head -n 1 || true)
  if [[ -n $args ]]; then
    args=${args//=/ }
    if [[ $args =~ --gpu-memory-utilization[[:space:]]+([0-9.]+) ]]; then
      printf '%s %s\n' "${BASH_REMATCH[1]}" "процесс ${args%% *}"
    else
      printf '%s %s\n' "0.9" "процесс ${args%% *} (значение по умолчанию vLLM)"
    fi
  fi
}

show_vram() {
  step "6. Видеопамять"
  local need line total_mib free_mib
  need=$(env_get "$FM_ENV_FILE" ASR_MAX_VRAM_GB)
  [[ -n $need ]] || need=$(env_get "$FM_ENV_EXAMPLE" ASR_MAX_VRAM_GB)
  need=${need:-4}

  nvidia-smi --query-gpu=index,name,memory.total,memory.used,memory.free \
    --format=csv,noheader,nounits | while IFS=, read -r idx gname total used free; do
    ok "GPU$idx$gname: всего $(awk -v m="$total" 'BEGIN{printf "%.1f", m/1024}') ГБ," \
      "занято $(awk -v m="$used" 'BEGIN{printf "%.1f", m/1024}') ГБ," \
      "свободно $(awk -v m="$free" 'BEGIN{printf "%.1f", m/1024}') ГБ"
  done
  nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader 2>/dev/null |
    while IFS= read -r app; do
      # "if", not "&&": a false test as the last command would end the script (set -e)
      if [[ -n $app ]]; then note "процесс на GPU: $app"; fi
    done

  # asr gets the first GPU (compose: count: 1)
  line=$(nvidia-smi --query-gpu=memory.total,memory.free --format=csv,noheader,nounits -i 0)
  IFS=, read -r total_mib free_mib <<<"$line"

  if [[ -n $(docker ps -q --filter "name=^${FM_PROJECT}-asr-" 2>/dev/null) ]]; then
    note "контейнер ${FM_PROJECT}-asr уже работает: занятая им память входит в «занято»."
  fi

  # asr measures the free memory after creating its CUDA context (~0.5 GB) and
  # uses min(ASR_MAX_VRAM_GB, free) as its budget: large_ctc if it fits, ctc otherwise.
  local large small budget
  large=$(env_get "$FM_ENV_FILE" ASR_MODEL_VRAM_GB)
  [[ -n $large ]] || large=$(env_get "$FM_ENV_EXAMPLE" ASR_MODEL_VRAM_GB)
  large=${large:-3.0}
  small=$(env_get "$FM_ENV_FILE" ASR_FALLBACK_VRAM_GB)
  [[ -n $small ]] || small=$(env_get "$FM_ENV_EXAMPLE" ASR_FALLBACK_VRAM_GB)
  small=${small:-1.5}
  budget=$(awk -v f="$free_mib" -v n="$need" 'BEGIN { b = f / 1024 - 0.5; if (b > n) b = n; printf "%.1f", b }')
  if awk -v b="$budget" -v l="$large" 'BEGIN { exit !(b >= l) }'; then
    ok "asr получит около $budget ГБ (ASR_MAX_VRAM_GB=$need, свободно минус ~0.5 ГБ на CUDA): хватает на large_ctc (~$large ГБ)"
    return
  fi
  if awk -v b="$budget" -v s="$small" 'BEGIN { exit !(b >= s) }'; then
    warn "asr получит около $budget ГБ: на large_ctc (~$large ГБ) не хватает, asr сам загрузит ctc (~$small ГБ)"
    note "Распознавание будет работать, но с чуть большим числом ошибок. Для large_ctc"
    note "освободите ещё $(awk -v b="$budget" -v l="$large" 'BEGIN{printf "%.1f", l - b}') ГБ видеопамяти (другие процессы на GPU, см. список выше)."
    return
  fi
  local missing
  missing=$(awk -v b="$budget" -v s="$small" 'BEGIN{printf "%.1f", s - b}')
  warn "asr получит около $budget ГБ: не хватает даже на ctc (~$small ГБ), asr не запустится"
  local util_info util who
  util_info=$(vllm_utilization)
  note "Освободите на GPU ещё $missing ГБ:"
  if [[ -n $util_info ]]; then
    util=${util_info%% *}
    who=${util_info#* }
    note "- уменьшить --gpu-memory-utilization у vLLM ($who, сейчас $util):"
    note "  каждые 0.01 = $(awk -v t="$total_mib" 'BEGIN{printf "%.2f", t/1024/100}') ГБ;"
  fi
  note "- или остановить/уменьшить другие процессы на GPU из списка выше."
  note "install.sh сам ничего на GPU не меняет."
  TODO+=("освободить $missing ГБ видеопамяти для asr (см. раздел 6 выше)")
}

# ---------------------------------------------------------------------------
check_host() {
  step "7. Прочее"
  local avail_gb dir=/var/lib/docker
  [[ -d $dir ]] || dir=/
  avail_gb=$(df -Pk "$dir" | awk 'NR == 2 { printf "%d", $4 / 1024 / 1024 }')
  if ((avail_gb < MIN_DISK_GB)); then
    warn "свободно на диске ($dir): $avail_gb ГБ; образы и модель займут около $MIN_DISK_GB ГБ"
  else
    ok "свободно на диске ($dir): $avail_gb ГБ"
  fi

  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then
    warn "включён ufw. LiveKit работает в сети хоста, поэтому ему нужны правила"
    note "(порты 80/443 Caddy Docker открывает сам). install.sh правила не меняет:"
    note "  sudo ufw allow 7881/tcp"
    note "  sudo ufw allow 7882/udp"
    note "  sudo ufw allow from 172.16.0.0/12 to any port 7880 proto tcp"
    TODO+=("добавить правила ufw для LiveKit (см. раздел 7 выше)")
  else
    ok "ufw не включён"
  fi

  local rmem
  rmem=$(cat /proc/sys/net/core/rmem_max 2>/dev/null || echo 0)
  if ((rmem < 5000000)); then
    note "LiveKit советует увеличить UDP-буферы (сейчас net.core.rmem_max=$rmem):"
    note "  sudo sysctl -w net.core.rmem_max=16777216 net.core.wmem_max=16777216"
  fi
}

# ---------------------------------------------------------------------------
check_nvidia_driver
ensure_docker
ensure_toolkit
check_gpu_in_docker
check_ports
show_vram
check_host

step "Итог"
if ((${#WARNINGS[@]} > 0)); then
  warn "предупреждения:"
  for item in "${WARNINGS[@]}"; do note "- $item"; done
fi
if ((${#TODO[@]} > 0)); then
  warn "осталось сделать:"
  for item in "${TODO[@]}"; do note "- $item"; done
fi
if ((${#PROBLEMS[@]} > 0)); then
  fail "есть проблемы, up.sh может не запуститься:"
  for item in "${PROBLEMS[@]}"; do note "- $item"; done
  exit 1
fi
ok "сервер готов. Дальше: sudo $FM_DEPLOY_DIR/up.sh"
