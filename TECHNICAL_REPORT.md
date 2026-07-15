# Technical report

All numbers below were **measured on the development machine** (specs at the bottom) on
2026-07-15, not quoted from model cards. Methods are stated inline so they can be re-run.

## Models and runtimes

| Role | Model | Params | Format / quantization | On-disk size |
|---|---|---|---|---|
| Generation | `qwen2.5:7b` | 7.6 B | GGUF, **Q4_K_M** (Ollama default) | 4.7 GB |
| Text embeddings | `BAAI/bge-small-en-v1.5` | 33 M | FP32 safetensors, 384-d | 129 MB |
| Image + text embeddings | `clip-ViT-B-32` | 151 M | FP32 safetensors, 512-d | 579 MB |

Runtimes: **Ollama** (llama.cpp under the hood) for generation, **sentence-transformers /
PyTorch 2.x** on CUDA-when-available for both embedders. The app itself is Python 3.13
frozen with cx_Freeze into a 250 MB MSI.

## Optimization choices

- **Q4_K_M quantization** for the LLM — the reason a 7.6 B model fits a 6 GB laptop GPU at
  all (5.1 GB resident when loaded).
- **Hardware-matched model selection** at first run: the setup wizard detects VRAM and
  refuses to recommend models that would spill to CPU.
- **Temperature 0.1** for answers, **0.0 + schema-constrained decoding** for filing plans —
  plan output is machine-parsed, so sampler creativity is pure downside.
- **bge-small over bge-base/large**: 33 M params embeds a chunk in ~2.5 ms; retrieval
  quality was sufficient to pass the calibration separation test (see EVALUATION.md).
- Embedding batch size 32 (documents) and CLIP batch size 16 (photos) — bounded so a folder
  of 24-megapixel photos doesn't balloon RAM.

## Measured inference latency

Method: `time.perf_counter()` around the public APIs, warm models, median of 5 for
single-item numbers. LLM numbers from `ollama run --verbose`.

| Operation | Latency |
|---|---|
| Embed one query (bge-small) | **13 ms** |
| Embed a 32-chunk batch | 79 ms (**~405 chunks/s**) |
| CLIP: embed one text query | **20 ms** |
| CLIP: index photos (1080p, batch 16) | **~12.6 photos/s** |
| LLM cold load (first question after boot) | 9.0 s |
| LLM prompt ingestion | 404 tokens/s |
| LLM generation | **27.6 tokens/s** |

End-to-end feel: a typical cited answer (retrieval + ~150 generated tokens) lands in
**6–8 s warm**; photo search over an indexed library is **sub-second**.

## Memory and processor usage

| Measurement | Value |
|---|---|
| App process with both embedding models loaded (RSS) | ~1.1 GB |
| qwen2.5:7b resident (Ollama, separate process) | 5.1 GB — **82 % GPU / 18 % CPU** on the 6 GB card |
| Installed footprint | ~1.2 GB app + 4.7 GB LLM + 0.7 GB embedders |

Both models load lazily — a user who never opens photo search never pays CLIP's memory,
and Ollama unloads the LLM after a few idle minutes.

GPU is used by both the LLM (via Ollama) and the embedders (PyTorch CUDA). Everything
degrades gracefully to CPU-only — the setup wizard just recommends a smaller model. No NPU
path.

## Tested device

| | |
|---|---|
| Machine | Windows 11 Home laptop (10.0.26200) |
| GPU | NVIDIA GeForce RTX 4050 Laptop, 6 GB VRAM |
| RAM | 16 GB (15.3 GB usable) |
| Python | 3.13, uv-managed venv |

## Local AI verification

**Everything the user's data touches runs on device.**

- The LLM is served by Ollama on `127.0.0.1:11434`; the client is
  [ollama_client.py](src/osdc/pipeline/llm/ollama_client.py) and its host is loopback.
- Embeddings and CLIP run in-process via sentence-transformers.
- Database (SQLite), vector store, logs and settings live in `%LOCALAPPDATA%\osdc`; filed
  copies live in `~/AI Library`.

**What requires internet, exactly once:** downloading model weights (Hugging Face for the
two embedders, Ollama's registry for qwen2.5) and the Ollama installer during first-run
setup. After that the app is fully functional offline.

**No user data leaves the device.** There is no telemetry, no account, no analytics, and no
code path that posts document content anywhere. The API server binds to localhost. This is
verifiable by grepping the source for outbound hosts — the only non-loopback URLs in the
codebase are the one-time model-download endpoints.
