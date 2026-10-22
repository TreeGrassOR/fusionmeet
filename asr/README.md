# FusionMeet ASR service

Speech recognition for FusionMeet's per-participant transcription. It is a FastAPI
service that serves several recognition models at once; each request picks one with
`?model=<id>`, and the room's transcription settings on the website choose which.
It is used only inside the compose network (`http://asr:8000`) and its port is never
published.

## Models

| id | Label (website) | What it is | Where it runs |
|---|---|---|---|
| `gigaam-v3` (default) | «Русский — GigaAM v3» | [GigaAM-v3](https://huggingface.co/ai-sage/GigaAM-v3) `e2e_rnnt`: Russian only, with punctuation, capitals and digits. English speech comes out as garbage. | GPU: torch, fp16 encoder, batched. CPU: [ONNX int8](https://huggingface.co/istupakov/gigaam-v3-onnx), one clip at a time. |
| `gigaam-multilingual` | «Прежняя модель — GigaAM Multilingual» | [GigaAM Multilingual](https://huggingface.co/ai-sage/GigaAM-Multilingual) `large_ctc`, the model of the previous release: lowercase, no punctuation; Russian, Kazakh, Kyrgyz, Uzbek, English. | GPU: torch, fp16 encoder, batched. CPU: [ONNX int8](https://huggingface.co/istupakov/gigaam-multilingual-large-ctc-onnx). |
| `gigaam-multilingual-punct` | «GigaAM Multilingual с пунктуацией» | The text of `gigaam-multilingual` passed through the [xlm-roberta punctuation and true-casing model](https://huggingface.co/1-800-BAD-CODE/xlm-roberta_punctuation_fullstop_truecase). | The recogniser of `gigaam-multilingual` (no second copy) plus the punctuation model on the CPU (ONNX fp32). |

Every model is chosen and pinned in `asr_service/registry.py` (repository and commit
SHA of the weights and, for GigaAM-v3 on the GPU, of the code). The choice follows a
CPU benchmark of the candidates; VibeVoice-ASR, Qwen3-ASR and Whisper were not taken
(too large for the VRAM budget, poor Russian or too slow on the CPU).

The GigaAM-v3 weights are loaded on the GPU through the `GigaAMModel` class of
GigaAM-Multilingual: the v3 remote code does not load on transformers 5.20.1.
`config._commit_hash` is set to the v3 revision so that `tokenizer.model` is taken
from that revision (without it, it would come from the `main` branch, and an offline
start would fail). In the ONNX exports of GigaAM-v3 the padding of a batch leaks into
the output, so on the CPU every model gets one clip per call.

### Resources

Measured on the CPU with every model loaded from the cache (4-vCPU Xeon,
`ASR_CPU_THREADS=2`), resident memory of the process with one model enabled, and
download size:

| id | RAM | Load time | Download (CPU) | Download (GPU) | VRAM (estimate) |
|---|---|---|---|---|---|
| `gigaam-v3` | 0.5 GB | 3.6 s | 0.22 GB | 0.45 GB | 1.5 GiB |
| `gigaam-multilingual` | 1.1 GB | 3.9 s | 0.59 GB | 2.3 GB | 3.0 GiB |
| `gigaam-multilingual-punct` | +1.3 GB (peak +1.8 GB while loading) | +11.5 s | +1.1 GB | +1.1 GB | — |

All three on the CPU: about 3.4 GB resident, 4.0 GB peak while loading. With the
defaults on a GPU (`gigaam-v3` on the GPU, the rest on the CPU), the first start
downloads about 2.1 GB. The VRAM figures are estimates (the budget check uses them, see "GPU and
CPU"); override them with `ASR_VRAM_GB_<ID>` once measured.

## API

### `POST /v1/transcribe?model=<id>`

- Header `Authorization: Bearer <ASR_API_KEY>`. A missing or wrong key gets a `401`.
- `model` is optional: without it (or empty) the default model (`ASR_DEFAULT_MODEL`)
  is used.
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
  - `200`: `{"text", "duration", "model_id", "model_label", "model", "revision",
    "variant", "processing_ms"}`. `model_id` and `model_label` are the model of the
    registry; `model`, `revision` and `variant` describe the weights that ran (they
    differ between the GPU and the CPU backend). Clips shorter than 0.1 s return
    `text: ""` and the model does not run.
  - `400`: unknown model, a model not enabled in `ASR_MODELS`, or a body that is not
    decodable (garbage, truncated header, empty body).
  - `413`: the audio is longer than the limit.
  - `422`: wrong sample rate, channel count, codec or media type.
  - `503`: the model is still loading (`Retry-After: 5`), it failed to load (the
    detail gives the error; no `Retry-After`), or `ASR_MAX_QUEUE` requests are
    already waiting for it (`Retry-After: 1`).
  - `500`: inference failed.
- The text is cleaned up: `<...>` tokens (such as `<unk>`) are removed,
  whitespace is normalised, and output without any letter or digit (a bare `.`) is
  returned as `""`.

```sh
curl -H "Authorization: Bearer $ASR_API_KEY" -H "Content-Type: audio/wav" \
     --data-binary @phrase.wav "http://asr:8000/v1/transcribe?model=gigaam-multilingual-punct"
```

### `GET /v1/models`

Header `Authorization: Bearer <ASR_API_KEY>`. The enabled models, in `ASR_MODELS`
order:

```json
{"default": "gigaam-v3",
 "models": [{"id": "gigaam-v3", "label": "Русский — GigaAM v3",
             "description": "Лучшее качество для русской речи, …",
             "languages": ["ru"], "punctuation": true,
             "device": "cuda", "status": "ready", "error": null}]}
```

`status` is `loading`, `ready` or `failed` (then `error` says why); `device` is
`cuda` or `cpu` once the model is ready, `null` before. Labels and descriptions are
in Russian, for the website.

### `GET /health`

No authentication. `200` once the default model is ready, `503` before (`"status":
"loading"`) or if it failed (`"status": "failed"`). The body always has the status of
every model (without error messages) and, for loaded models, the weights that run:

```json
{"status": "ok", "default": "gigaam-v3",
 "models": [{"id": "gigaam-v3", "status": "ready", "device": "cuda",
             "model": "ai-sage/GigaAM-v3", "revision": "7655ad71…",
             "variant": "v3_e2e_rnnt", "fp16_encoder": true}, …],
 "vram_limit_gb": 4.0, "vram_free_gb_at_start": 7.9, "cpu_threads": 4,
 "max_batch_size": 8}
```

The Docker `HEALTHCHECK` calls this endpoint.

## Loading

Every enabled model is loaded at startup, the default model first, then the others
in `ASR_MODELS` order: switching models never loads anything. The server answers
while they load. A model that cannot be loaded (a download error, for example) is
reported as `failed` with its error in the log and in `/v1/models`, and the other
models keep serving; restart the service to retry it. A model that fails only on the
GPU runs on the CPU instead (see "GPU and CPU"). Configuration errors (an
unknown model id, a default model that is not enabled) stop the service at once with
status 1.

Weights are downloaded once into `HF_HOME=/models` (mount a volume there), by commit
SHA, so later starts load them from the volume without any request to the Hub. The
torch weights use the Hugging Face cache (`/models/hub`). The ONNX files are plain
files in `/models/onnx/<owner>--<name>/<revision>/`: onnxruntime refuses external
weights (`*.onnx.data`) that resolve outside the directory of their
`.onnx` file, which is what the symlinks of the Hugging Face cache do. Set `HF_TOKEN`
if the Hub rate-limits the first download.

## GPU and CPU

With `ASR_DEVICE=cuda` (the default) the service does the following at startup:

1. It reads the free and total memory of the GPU with `torch.cuda.mem_get_info()`
   (this creates the CUDA context) and sets `budget = min(ASR_MAX_VRAM_GB, free)`.
   When no enabled model has a GPU backend, it skips this: torch is not imported
   and the GPU is not touched.
2. It places the models that have a GPU backend (`gigaam-v3`, `gigaam-multilingual`)
   on the GPU in priority order (the default model first, then `ASR_MODELS` order)
   while their VRAM needs (`ASR_VRAM_GB_<ID>`) fit in what is left of the budget.
   A model that does not fit runs on the CPU, with a `WARNING` that says how much it
   needs and how much is left. With the defaults (4 GiB) `gigaam-v3` (1.5 GiB) goes
   to the GPU and `gigaam-multilingual` (3.0 GiB) to the CPU.
3. If CUDA is not usable (no GPU visible, torch not working), every model runs on the
   CPU with a `WARNING`; the service no longer exits.
4. It calls `torch.cuda.set_per_process_memory_fraction(budget / total)`, so
   PyTorch's allocator cannot use more than the budget. The CUDA context, about
   0.3–0.5 GB, comes on top of this limit.
5. It loads each GPU model with only the encoder cast to fp16
   (`ASR_FP16_ENCODER=true`), before it is moved to the GPU; calling `model.half()`
   would break the fp32 mel front-end and the CTC/RNN-T heads. A warm-up batch of the
   largest allowed size follows, so a budget that is too small fails at startup and
   not during a meeting. If the fp16 warm-up fails with an error other than out of
   memory, it retries in fp32. Any error while loading a model on the GPU (out of
   memory, a CUDA or driver error, a download error) frees its GPU memory and loads
   that model's CPU backend instead, with a `WARNING` (the traceback of an error
   other than out of memory is logged too); `/health` and `/v1/models` then show
   `"device": "cpu"` for it.

With `ASR_DEVICE=cpu` every model runs on the CPU and torch is not even imported.

On the CPU each model has its own onnxruntime sessions with `ASR_CPU_THREADS`
intra-op threads (default: half of the CPUs) and one inter-op thread; torch is
limited to the same number of threads. The CPU models share the cores: when several
are busy at once, each one is slower.

## Batching

Each model has its own queue (`ASR_MAX_QUEUE` clips) and its own worker thread, so a
busy model does not delay another one. `gigaam-multilingual-punct` shares the queue
of `gigaam-multilingual`, then the punctuation model runs in its own thread.

On the GPU, one worker thread runs the model on batches of the queued requests. A
batch is closed when one of these happens:

- it holds `ASR_MAX_BATCH_SIZE` clips;
- `ASR_BATCH_WAIT_MS` has passed since its first clip;
- the padded batch (clips × longest clip) would exceed
  `ASR_MAX_BATCH_AUDIO_SECONDS`.

Requests that arrive while the GPU is busy form the next batch. On the CPU every
model takes one clip per call (batching was slower there, and wrong for the GigaAM-v3
exports). A queued clip whose client has disconnected (for example, the agent timed
out and retries) is dropped before it reaches the model. Such a request gets no line
in the access log, because the server drops a response to a closed connection.

## Configuration

An empty variable means its default.

| Variable | Default | Meaning |
|---|---|---|
| `ASR_API_KEY` | (required) | Shared Bearer key. The service does not start without it. |
| `ASR_MODELS` | `gigaam-v3,gigaam-multilingual,gigaam-multilingual-punct` | Enabled models, comma-separated (order of `/v1/models` and of loading) |
| `ASR_DEFAULT_MODEL` | `gigaam-v3` | Model of requests without `?model=`; must be enabled |
| `ASR_DEVICE` | `cuda` | `cuda`, `cuda:<n>` or `cpu` (everything on the CPU) |
| `ASR_MAX_VRAM_GB` | `4` | Upper limit of this process's VRAM budget (GiB) |
| `ASR_VRAM_GB_GIGAAM_V3` | `1.5` | VRAM needed by `gigaam-v3` on the GPU (GiB, an estimate) |
| `ASR_VRAM_GB_GIGAAM_MULTILINGUAL` | `3.0` | VRAM needed by `gigaam-multilingual` on the GPU (GiB) |
| `ASR_CPU_THREADS` | half of the CPUs, at least 1 | Intra-op threads per onnxruntime session; torch threads |
| `ASR_FP16_ENCODER` | `true` | fp16 encoder weights on CUDA |
| `ASR_MAX_BATCH_SIZE` | `8` | Clips per model call on the GPU |
| `ASR_BATCH_WAIT_MS` | `20` | How long a GPU batch waits for more clips |
| `ASR_MAX_BATCH_AUDIO_SECONDS` | `240` | Padded audio per GPU batch |
| `ASR_MAX_AUDIO_SECONDS` | `30` | Longest accepted clip |
| `ASR_MAX_QUEUE` | `64` | Waiting clips per model before `503` |
| `ASR_PORT` | `8000` | Listening port inside the container |
| `ASR_LOG_LEVEL` | `info` | `debug` also logs every batch |
| `HF_HOME` | `/models` | Hugging Face cache and ONNX files (volume) |

`ASR_MODEL_ID`, `ASR_MODEL_REVISION`, `ASR_MODEL_VARIANT`, `ASR_MODEL_VRAM_GB`,
`ASR_FALLBACK_REVISION`, `ASR_FALLBACK_VARIANT` and `ASR_FALLBACK_VRAM_GB` of the
single-model service are removed: the models and their revisions come from the
registry. When one of them is set, the service logs a `WARNING` and ignores it. The
old CPU fallback variant (`ctc`) is replaced by the CPU backends above.

## Image

`docker build -t fusionmeet/asr:local asr/` builds on
`nvidia/cuda:12.8.2-base-ubuntu24.04` with Python 3.12, `torch==2.10.0+cu128`,
`torchaudio==2.10.0+cu128` and `transformers==5.20.1` (the versions the GigaAM model
card recommends) for the GPU, and `onnx-asr==0.12.0` with the CPU build of
`onnxruntime==1.30.0` for the CPU. `deploy/compose.yaml` uses the same image name.
The container runs as the unprivileged user `asr` (uid 10001).

The host needs the NVIDIA Container Toolkit and an NVIDIA driver for the GPU models
(without a GPU, every model runs on the CPU). The compose service needs a GPU
reservation. The torch wheels bundle the CUDA 12.8 runtime and libraries, so the
driver is the only CUDA component the host provides:

- Driver ≥ 570 (the CUDA 12.8 driver branch) runs it without any compatibility mode.
- Drivers from 525.60.13 to 569.x should work through CUDA minor version
  compatibility, which runs a CUDA 12.x build on any driver ≥ 525.60.13. This has
  not been tested with this service. NVIDIA lists two limits: PTX cannot be
  JIT-compiled, and CUDA features that need a newer driver fail with
  `cudaErrorCallRequiresNewerDriver`. The cu128 wheels ship SASS for sm_70, 75,
  80, 86, 90, 100 and 120, so an RTX 3090 (sm_86) needs no PTX.
- Older drivers do not support CUDA 12 and cannot run the GPU models.

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
pip install -r requirements-dev.txt      # no torch/onnxruntime: the tests mock the models
pytest && ruff check . && ruff format --check .
```

`requirements.txt` and `requirements-dev.txt` pin the whole dependency tree. They
are generated from the `.in` files with the `uv pip compile` command written at
the top of each file.

To run the real models on the CPU, install `torch==2.10.0+cpu` and
`torchaudio==2.10.0+cpu` from `https://download.pytorch.org/whl/cpu` with the rest
of `requirements.txt` (the `nvidia-*`, `cuda-*` and `triton` lines are not needed).
Then start the service (about 4.4 GB is downloaded into `HF_HOME` the first time;
`ASR_MODELS` can be trimmed):

```sh
ASR_API_KEY=dev ASR_DEVICE=cpu HF_HOME=$PWD/.models \
  uvicorn asr_service.main:create_app --factory --port 8000
```
