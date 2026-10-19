# Speech recognition service with several selectable models (see README.md): torch
# runs the GigaAM models on the GPU, onnxruntime (CPU build) every model on the CPU.
#
# The CUDA user-space libraries come with the torch wheels (nvidia-* packages), so
# the "base" CUDA image is enough; the NVIDIA Container Toolkit injects the driver.
# Driver: >= 570 is the CUDA 12.8 driver branch; >= 525.60.13 runs the CUDA 12.8
# wheels through CUDA minor version compatibility (see README.md, "Image").
FROM nvidia/cuda:12.8.2-base-ubuntu24.04

# The base image sets NVIDIA_REQUIRE_CUDA="cuda>=12.8 ...", so the NVIDIA runtime
# refuses to start the container on a GeForce card with a driver older than 570.
# The torch cu128 wheels bundle their own CUDA 12.8 runtime and SASS for the
# common GPUs (sm_86 for the RTX 3090), so a 12.x driver (>= 525.60.13) is enough
# under CUDA minor version compatibility, and the host driver (which a running
# vLLM uses too) does not have to be upgraded. deploy/install.sh checks it.
ENV NVIDIA_DISABLE_REQUIRE=true

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    HF_HOME=/models \
    HF_HUB_DISABLE_TELEMETRY=1 \
    ASR_PORT=8000 \
    ASR_LOG_LEVEL=info

# Ubuntu 24.04 ships Python 3.12
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3.12 python3.12-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3.12 -m venv /opt/venv

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && pip check \
    && rm /tmp/requirements.txt

# Un-privileged user; /models keeps the Hugging Face cache (weights and the model's
# remote code) in a volume, so the weights are downloaded only once.
RUN groupadd --system --gid 10001 asr \
    && useradd --system --uid 10001 --gid asr --home-dir /app --shell /usr/sbin/nologin asr \
    && mkdir -p /models \
    && chown asr:asr /models

WORKDIR /app
COPY asr_service /app/asr_service

USER asr
VOLUME ["/models"]

# Reachable on the compose network only: the port is never published.
EXPOSE 8000

# The first start downloads the weights (about 5 GB for all the models; /health is
# ready once the default model is): long start period.
HEALTHCHECK --interval=15s --timeout=5s --start-period=900s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request as r; r.build_opener(r.ProxyHandler({})).open('http://127.0.0.1:%s/health' % os.environ.get('ASR_PORT', '8000'), timeout=4)"]

CMD ["sh", "-c", "exec uvicorn asr_service.main:create_app --factory --host 0.0.0.0 --port \"$ASR_PORT\" --log-level \"$ASR_LOG_LEVEL\""]
