# FusionMeet ASR service

Speech recognition for FusionMeet's per-participant transcription. It is a FastAPI
service around [GigaAM Multilingual](https://huggingface.co/ai-sage/GigaAM-Multilingual)
(CTC, MIT licence) that runs on the GPU. It is used only inside the compose network
(`http://asr:8000`) and its port is never published.

The model outputs lowercase text without punctuation or digits. It recognises
Russian, Kazakh, Kyrgyz, Uzbek and English, and has no language parameter.

## API

### `POST /v1/transcribe`

- Header `Authorization: Bearer <ASR_API_KEY>`. A missing or wrong key gets a `401`.
- The body is the raw audio, not multipart. It must be 16 kHz mono and at most
  `ASR_MAX_AUDIO_SECONDS` (30 s) long:
  - `Content-Type: audio/wav` (also `audio/x-wav`, `audio/wave`): RIFF WAV with
    16-bit PCM or 32-bit float samples.
  - `Content-Type: audio/pcm`, `audio/l16` or `application/octet-stream`: raw
    signed 16-bit little-endian PCM. The optional `rate=` and `channels=`
    parameters are checked when present. `audio/l16` is read as little-endian too.
  - With no `Content-Type`, a body that starts with `RIFF` is read as WAV and
    anything else as raw PCM.
- Responses:
  - `200`:
    `{"text", "duration", "model", "revision", "variant", "processing_ms"}`.
    Clips shorter than 0.1 s return `text: ""` and the model does not run.
  - `400`: the body is not decodable (garbage, truncated header, empty body).
  - `413`: the audio is longer than the limit.
  - `422`: wrong sample rate, channel count, codec or media type.
  - `503`: the model is still loading, or `ASR_MAX_QUEUE` requests are already
    waiting (`Retry-After` is set).
  - `500`: inference failed.

```sh
curl -H "Authorization: Bearer $ASR_API_KEY" -H "Content-Type: audio/wav" \
     --data-binary @phrase.wav http://asr:8000/v1/transcribe
```

### `GET /health`

No authentication.

- `503 {"status": "loading"}` while the weights download or load.
- Then `200 {"status": "ok", "model", "revision", "variant", "device",
  "fp16_encoder", "vram_limit_gb", "vram_free_gb_at_start", "max_batch_size"}`.

The Docker `HEALTHCHECK` calls this endpoint.

## Batching

Concurrent requests are queued, and one worker thread runs the model on batches of
them. A batch is closed when one of these happens:

- it holds `ASR_MAX_BATCH_SIZE` clips;
- `ASR_BATCH_WAIT_MS` has passed since its first clip;
- the padded batch (clips × longest clip) would exceed
  `ASR_MAX_BATCH_AUDIO_SECONDS`.

Requests that arrive while the GPU is busy form the next batch. Batched results
match single-clip results (checked on CPU). A queued clip whose client has
disconnected (for example, the agent timed out and retries) is dropped before it
reaches the model. Such a request gets no line in the access log, because the
server drops a response to a closed connection.

## GPU memory

At startup on CUDA, the service does the following:

1. It reads the free and total memory with `torch.cuda.mem_get_info()` and sets
   `budget = min(ASR_MAX_VRAM_GB, free)`.
2. It checks the budget:
   - If the budget is at least `ASR_MODEL_VRAM_GB`, it loads the primary model
     (`large_ctc`, 585M parameters).
   - Otherwise, if the budget is at least `ASR_FALLBACK_VRAM_GB`, it loads the
     fallback (`ctc`, 221M) and logs a `WARNING` that says why.
   - Otherwise it exits with status 1. The log gives the free memory, the memory
     each variant needs and what to change (for example, lower
     `--gpu-memory-utilization` of the vLLM server on the same GPU).
3. It calls `torch.cuda.set_per_process_memory_fraction(budget / total)`, so
   PyTorch's allocator cannot use more than the budget. The CUDA context, about
   0.3–0.5 GB, comes on top of this limit.
4. It loads the model. If loading the primary raises a CUDA out-of-memory error,
   it frees the memory and loads the fallback. With `ASR_FP16_ENCODER=true`, only
   the encoder is cast to fp16, before it is moved to the GPU. Calling
   `model.half()` would break the fp32 mel front-end and the CTC head.
5. It runs a warm-up batch of the largest allowed size, so a budget that is too
   small fails at startup and not during a meeting. If the fp16 warm-up fails with
   an error other than out-of-memory, it retries in fp32.

The weights download once into `HF_HOME=/models`; mount a volume there. They are
2.3 GB for `large_ctc` and 0.9 GB for `ctc`. Revisions are pinned by commit SHA,
so later starts load from the cache.

## Configuration

An empty variable means its default, except `ASR_FALLBACK_REVISION`, where it
disables the fallback.

| Variable | Default | Meaning |
|---|---|---|
| `ASR_API_KEY` | (required) | Shared Bearer key. The service does not start without it. |
| `ASR_MODEL_ID` | `ai-sage/GigaAM-Multilingual` | Hugging Face repository |
| `ASR_MODEL_REVISION` | `3905cd51c3ed4e88c8edf33f3302969ba480a327` | Primary revision: branch `large_ctc` |
| `ASR_MODEL_VARIANT` | `large_ctc` | Label reported in responses and logs |
| `ASR_MODEL_VRAM_GB` | `3.0` | VRAM needed by the primary (GiB) |
| `ASR_FALLBACK_REVISION` | `2f8a57144e6ec3adfd32fe0484d9ea9913305bc8` | Fallback revision: branch `ctc`. Empty disables it. |
| `ASR_FALLBACK_VARIANT` | `ctc` | Label of the fallback |
| `ASR_FALLBACK_VRAM_GB` | `1.5` | VRAM needed by the fallback (GiB) |
| `ASR_MAX_VRAM_GB` | `4` | Upper limit of this process's VRAM budget (GiB) |
| `ASR_DEVICE` | `cuda` | `cuda`, `cuda:<n>` or `cpu` (`cpu` is for tests only) |
| `ASR_FP16_ENCODER` | `true` | fp16 encoder weights on CUDA |
| `ASR_MAX_BATCH_SIZE` | `8` | Clips per model call |
| `ASR_BATCH_WAIT_MS` | `20` | How long a batch waits for more clips |
| `ASR_MAX_BATCH_AUDIO_SECONDS` | `240` | Padded audio per batch |
| `ASR_MAX_AUDIO_SECONDS` | `30` | Longest accepted clip |
| `ASR_MAX_QUEUE` | `64` | Waiting clips before `503` |
| `ASR_PORT` | `8000` | Listening port inside the container |
| `ASR_LOG_LEVEL` | `info` | `debug` also logs every batch |
| `HF_HOME` | `/models` | Hugging Face cache (volume) |

## Image

`docker build -t fusionmeet/asr:local asr/` builds on
`nvidia/cuda:12.8.2-base-ubuntu24.04` with Python 3.12, `torch==2.10.0+cu128`,
`torchaudio==2.10.0+cu128` and `transformers==5.20.1`, the versions the model card
recommends. `deploy/compose.yaml` uses the same image name. The container runs as
the unprivileged user `asr` (uid 10001).

The host needs the NVIDIA Container Toolkit and an NVIDIA driver. The compose
service needs a GPU reservation. The torch wheels bundle the CUDA 12.8 runtime and
libraries, so the driver is the only CUDA component the host provides:

- Driver ≥ 570 (the CUDA 12.8 driver branch) runs it without any compatibility mode.
- Drivers from 525.60.13 to 569.x should work through CUDA minor version
  compatibility, which runs a CUDA 12.x build on any driver ≥ 525.60.13. This has
  not been tested with this service. NVIDIA lists two limits: PTX cannot be
  JIT-compiled, and CUDA features that need a newer driver fail with
  `cudaErrorCallRequiresNewerDriver`. The cu128 wheels ship SASS for sm_70, 75,
  80, 86, 90, 100 and 120, so an RTX 3090 (sm_86) needs no PTX.
- Older drivers do not support CUDA 12 and cannot run the service.

The base image sets `NVIDIA_REQUIRE_CUDA=cuda>=12.8 …`. Its exceptions for older
driver branches cover only data-center and professional brands, so on a GeForce
card the NVIDIA runtime refuses to start the container with a driver older than
570. The Dockerfile sets `NVIDIA_DISABLE_REQUIRE=true` to turn this check off, so
the host driver (which may also serve a running vLLM) does not have to be
upgraded. `deploy/install.sh` checks the driver version instead.

## Development

```sh
cd asr
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt      # no torch needed: the tests mock the model
pytest && ruff check . && ruff format --check .
```

`requirements.txt` and `requirements-dev.txt` pin the whole dependency tree. They
are generated from the `.in` files with the `uv pip compile` command written at
the top of each file.

To run the real model on the CPU, install `torch==2.10.0+cpu` and
`torchaudio==2.10.0+cpu` from `https://download.pytorch.org/whl/cpu` with the rest
of `requirements.txt`. Then start the service:

```sh
ASR_API_KEY=dev ASR_DEVICE=cpu ASR_MODEL_REVISION=2f8a57144e6ec3adfd32fe0484d9ea9913305bc8 \
  ASR_MODEL_VARIANT=ctc uvicorn asr_service.main:create_app --factory --port 8000
```
