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
#   6. показывает свободную видеопамять и какие модели распознавания (ASR_MODELS)
#      поместятся на GPU в пределах ASR_MAX_VRAM_GB;
#   7. проверяет, хватит ли оперативной памяти моделям распознавания на CPU.
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

# Recognition models of asr (asr/asr_service/registry.py) and their parts: the
# recogniser first ("-punct" shares the one of gigaam-multilingual), then the
# punctuation model. VRAM: recognisers that can run on the GPU (estimates,
# ASR_VRAM_GB_<ID> in deploy/.env). RAM: each part on the CPU (measured, peak while
# loading for the punctuation model, asr/README.md).
RECOGNITION_MODELS=(gigaam-v3 parakeet-v3 gigaam-multilingual gigaam-multilingual-punct)
declare -A MODEL_PARTS=(
  [gigaam-v3]=gigaam-v3
  [parakeet-v3]=parakeet-v3
  [gigaam-multilingual]=gigaam-multilingual
  [gigaam-multilingual-punct]="gigaam-multilingual punctuation"
)
declare -A PART_VRAM_KEY=(
  [gigaam-v3]=ASR_VRAM_GB_GIGAAM_V3
  [gigaam-multilingual]=ASR_VRAM_GB_GIGAAM_MULTILINGUAL
)
declare -A PART_VRAM_GB=([gigaam-v3]=1.5 [gigaam-multilingual]=3.0)
declare -A PART_RAM_GB=([gigaam-v3]=0.5 [parakeet-v3]=2.7 [gigaam-multilingual]=1.1 [punctuation]=1.8)
RAM_RESERVE_GB=2 # free RAM wanted on top of the models
ENABLED_MODELS=() # enabled models, in the order asr loads them

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

# setting KEY DEFAULT: KEY of deploy/.env, else of .env.example, else DEFAULT
setting() {
  local value
  value=$(env_get "$FM_ENV_FILE" "$1")
  [[ -n $value ]] || value=$(env_get "$FM_ENV_EXAMPLE" "$1")
  printf '%s' "${value:-$2}"
}

# join_list ITEMS...: "a, b, c"
join_list() {
  local out
  out=$(printf '%s, ' "$@")
  printf '%s' "${out%, }"
}

# gb_floor X: X GB rounded down to 0.1 for messages (never more than there is)
gb_floor() {
  awk -v x="$1" 'BEGIN { printf "%.1f", int(x * 10 + 1e-6) / 10 }'
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
    PROBLEMS+=("драйвер NVIDIA $driver ниже $MIN_DRIVER_VERSION: asr (CUDA 12.8) не сможет использовать GPU, все модели распознавания будут работать на CPU")
    fail "драйвер $driver: нужен $MIN_DRIVER_VERSION или новее (CUDA 12.x), рекомендуется $CUDA128_DRIVER_VERSION+"
  elif ! version_ge "$driver" "$CUDA128_DRIVER_VERSION"; then
    WARNINGS+=("драйвер NVIDIA $driver ниже $CUDA128_DRIVER_VERSION: asr полагается на совместимость минорных версий CUDA и на таком драйвере не проверялся")
    warn "драйвер $driver ниже $CUDA128_DRIVER_VERSION (CUDA 12.8)"
    note "asr (torch с CUDA 12.8) должен работать через совместимость минорных версий CUDA"
    note "(CUDA minor version compatibility, драйвер $MIN_DRIVER_VERSION+), но на драйверах"
    note "ниже $CUDA128_DRIVER_VERSION не проверялся. Обновлять драйвер ради asr не обязательно"
    note "(обновление остановит vLLM). Если asr не сможет использовать GPU, смотрите журнал asr и"
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
  need=$(setting ASR_MAX_VRAM_GB 4)
  load_asr_models

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

  local asr_device
  asr_device=$(setting ASR_DEVICE cuda)
  if [[ ${asr_device,,} == cpu ]]; then
    ok "ASR_DEVICE=cpu: все модели распознавания работают на CPU, видеокарта не используется"
    return
  fi

  # asr measures the free memory after creating its CUDA context (~0.5 GB) and
  # uses min(ASR_MAX_VRAM_GB, free) as its budget. The recognisers that can run on
  # the GPU take it in load order (the default model first) while they fit; the
  # others run on the CPU.
  local budget left model rec vram first='' first_vram='' on_gpu=() on_cpu=()
  local -A device=()
  budget=$(awk -v f="$free_mib" -v n="$need" 'BEGIN { b = f / 1024 - 0.5; if (b > n) b = n; if (b < 0) b = 0; printf "%.3f", b }')
  ok "asr получит около $(gb_floor "$budget") ГБ (ASR_MAX_VRAM_GB=$need, свободно минус ~0.5 ГБ на контекст CUDA)"
  left=$budget
  for model in "${ENABLED_MODELS[@]}"; do
    rec=${MODEL_PARTS[$model]%% *}
    if [[ -z ${device[$rec]:-} ]]; then
      device[$rec]=cpu
      if [[ -n ${PART_VRAM_KEY[$rec]:-} ]]; then
        vram=$(setting "${PART_VRAM_KEY[$rec]}" "${PART_VRAM_GB[$rec]}")
        if [[ -z $first ]]; then
          first=$rec
          first_vram=$vram
        fi
        if awk -v l="$left" -v v="$vram" 'BEGIN { exit !(l >= v) }'; then
          device[$rec]=gpu
          left=$(awk -v l="$left" -v v="$vram" 'BEGIN { printf "%.3f", l - v }')
        elif [[ $rec != "$first" ]]; then
          note "$rec (~$vram ГБ) не помещается в оставшиеся ~$(gb_floor "$left") ГБ: работает на CPU"
        fi
      fi
    fi
    if [[ ${device[$rec]} == gpu ]]; then on_gpu+=("$model"); else on_cpu+=("$model"); fi
  done

  if ((${#on_gpu[@]} > 0)); then ok "на GPU: $(join_list "${on_gpu[@]}")"; fi
  if ((${#on_cpu[@]} > 0)); then ok "на CPU: $(join_list "${on_cpu[@]}")"; fi
  if [[ -z $first ]]; then
    note "Включённым моделям (ASR_MODELS) GPU не нужна."
    return
  fi
  [[ ${device[$first]} == cpu ]] || return 0

  if ((${#on_gpu[@]} == 0)); then
    warn "на $first (~$first_vram ГБ) видеопамяти не хватает: все модели будут работать на CPU"
  else
    warn "на $first (~$first_vram ГБ) видеопамяти не хватает: она будет работать на CPU"
  fi
  note "Распознавание работает и так, но медленнее, и CPU нагружен сильнее."
  WARNINGS+=("видеопамяти не хватает на $first: asr распознаёт на CPU (см. раздел 6 выше)")
  local missing todo="Чтобы $first работала на GPU,"
  if awk -v n="$need" -v v="$first_vram" 'BEGIN { exit !(n < v) }'; then
    note "$todo увеличьте ASR_MAX_VRAM_GB в $FM_ENV_FILE хотя бы до $first_vram."
    todo="Кроме того,"
  fi
  # rounded up to 0.1, so that freeing this much is enough
  missing=$(awk -v f="$free_mib" -v v="$first_vram" 'BEGIN { m = v - (f / 1024 - 0.5); printf "%.1f", (m > 0 ? int(m * 10 + 0.999999) / 10 : 0) }')
  awk -v m="$missing" 'BEGIN { exit !(m > 0) }' || return 0
  local util_info util who
  util_info=$(vllm_utilization)
  note "$todo освободите ещё $missing ГБ:"
  if [[ -n $util_info ]]; then
    util=${util_info%% *}
    who=${util_info#* }
    note "- уменьшить --gpu-memory-utilization у vLLM ($who, сейчас $util):"
    note "  каждые 0.01 = $(awk -v t="$total_mib" 'BEGIN{printf "%.2f", t/1024/100}') ГБ;"
  fi
  note "- или остановить/уменьшить другие процессы на GPU из списка выше."
  note "install.sh сам ничего на GPU не меняет."
}

# ---------------------------------------------------------------------------
# load_asr_models: ASR_MODELS in the order asr loads them (ASR_DEFAULT_MODEL
# first) into ENABLED_MODELS; ids that asr would reject are recorded as problems.
load_asr_models() {
  local raw default item items=() models=() known=" ${RECOGNITION_MODELS[*]} "
  raw=$(setting ASR_MODELS "$(IFS=,; printf '%s' "${RECOGNITION_MODELS[*]}")")
  default=$(setting ASR_DEFAULT_MODEL gigaam-v3)
  read -r default <<<"${default,,}" # trim
  IFS=, read -r -a items <<<"${raw,,}"
  for item in "${items[@]}"; do
    read -r item <<<"$item" # trim
    [[ -n $item ]] || continue
    if [[ $known != *" $item "* ]]; then
      fail "ASR_MODELS: неизвестная модель $item (есть: $(join_list "${RECOGNITION_MODELS[@]}"))"
      PROBLEMS+=("ASR_MODELS: неизвестная модель $item, asr не запустится")
      continue
    fi
    if [[ " ${models[*]} " == *" $item "* ]]; then
      fail "ASR_MODELS: модель $item указана дважды"
      PROBLEMS+=("ASR_MODELS: модель $item указана дважды, asr не запустится")
      continue
    fi
    models+=("$item")
  done
  ENABLED_MODELS=("${models[@]}")
  if [[ " ${models[*]} " != *" $default "* ]]; then
    fail "ASR_DEFAULT_MODEL=$default не входит в ASR_MODELS"
    PROBLEMS+=("ASR_DEFAULT_MODEL=$default не входит в ASR_MODELS, asr не запустится")
    return
  fi
  ENABLED_MODELS=("$default")
  for item in "${models[@]}"; do
    [[ $item == "$default" ]] || ENABLED_MODELS+=("$item")
  done
}

# ---------------------------------------------------------------------------
check_ram() {
  step "7. Оперативная память"
  local avail_kb avail asr_id asr_gb model part sizes=() need want sizes_info=()
  local -A counted=()
  avail_kb=$(awk '$1 == "MemAvailable:" { print $2 }' /proc/meminfo)
  avail=$(awk -v k="${avail_kb:-0}" 'BEGIN { printf "%.1f", k / 1024 / 1024 }')

  # A running asr already holds its models: count its memory as available
  asr_id=$(docker ps -q --filter "name=^${FM_PROJECT}-asr-" 2>/dev/null | head -n 1 || true)
  if [[ -n $asr_id ]]; then
    # "1.5GiB / 15.6GiB" -> 1.5
    asr_gb=$(docker stats --no-stream --format '{{.MemUsage}}' "$asr_id" 2>/dev/null | awk '
      NR == 1 {
        n = $1 + 0; u = $1; sub(/^[0-9.]+/, "", u); f = 1 / 1024 / 1024 / 1024
        if (u ~ /^[kK]/) f = 1 / 1024 / 1024; else if (u ~ /^M/) f = 1 / 1024
        else if (u ~ /^G/) f = 1; else if (u ~ /^T/) f = 1024
        printf "%.1f", n * f
      }' || true)
    asr_gb=${asr_gb:-0}
    avail=$(awk -v a="$avail" -v u="$asr_gb" 'BEGIN { printf "%.1f", a + u }')
    note "контейнер ${FM_PROJECT}-asr уже работает: его $asr_gb ГБ считаются доступными."
  fi

  for model in "${ENABLED_MODELS[@]}"; do
    for part in ${MODEL_PARTS[$model]}; do
      [[ -z ${counted[$part]:-} ]] || continue
      counted[$part]=1
      sizes+=("${PART_RAM_GB[$part]}")
    done
  done
  need=$(printf '%s\n' "${sizes[@]}" 0 | awk '{ s += $1 } END { printf "%.1f", s }')
  want=$(awk -v n="$need" -v r="$RAM_RESERVE_GB" 'BEGIN { printf "%.1f", n + r }')
  if awk -v a="$avail" -v w="$want" 'BEGIN { exit !(a >= w) }'; then
    ok "доступно $avail ГБ: моделям распознавания на CPU нужно около $need ГБ (ASR_MODELS), запас есть"
    return
  fi
  warn "доступно $avail ГБ, а моделям распознавания на CPU нужно около $need ГБ и ещё ~$RAM_RESERVE_GB ГБ запаса"
  note "Иначе сервер может уйти в swap, а asr — завершиться из-за нехватки памяти."
  for model in "${RECOGNITION_MODELS[@]}"; do
    sizes=()
    for part in ${MODEL_PARTS[$model]}; do sizes+=("${PART_RAM_GB[$part]}"); done
    sizes_info+=("$model ~$(join_list "${sizes[@]}" | sed 's/, / + /g') ГБ")
  done
  note "Уберите лишние модели из ASR_MODELS в $FM_ENV_FILE (по умолчанию включены все четыре):"
  note "$(join_list "${sizes_info[@]}")"
  note "(gigaam-multilingual и gigaam-multilingual-punct делят одну модель)."
  [[ -f $FM_ENV_FILE ]] || note "Файл создаёт sudo $FM_DEPLOY_DIR/up.sh --env-only."
  WARNINGS+=("мало оперативной памяти для моделей распознавания: доступно $avail ГБ, нужно около $want ГБ (см. раздел 7 выше)")
}

# ---------------------------------------------------------------------------
check_host() {
  step "8. Прочее"
  local avail_gb dir=/var/lib/docker
  [[ -d $dir ]] || dir=/
  avail_gb=$(df -Pk "$dir" | awk 'NR == 2 { printf "%d", $4 / 1024 / 1024 }')
  if ((avail_gb < MIN_DISK_GB)); then
    warn "свободно на диске ($dir): $avail_gb ГБ; образы и модели займут около $MIN_DISK_GB ГБ"
  else
    ok "свободно на диске ($dir): $avail_gb ГБ"
  fi

  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then
    warn "включён ufw. LiveKit работает в сети хоста, поэтому ему нужны правила"
    note "(порты 80/443 Caddy Docker открывает сам). install.sh правила не меняет:"
    note "  sudo ufw allow 7881/tcp"
    note "  sudo ufw allow 7882/udp"
    note "  sudo ufw allow from 172.16.0.0/12 to any port 7880 proto tcp"
    TODO+=("добавить правила ufw для LiveKit (см. раздел 8 выше)")
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
check_ram
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
