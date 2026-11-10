# Spike T-007: local vision model (LM Studio, Qwen3.8 27B)

Measured 2026-11-10 on RTX 4090 24 GB, Windows 11. Helper: `tools/vlm.py`. Synthetic frames and results live in `H:\BelfastData\spikes\vlm\` (not in git).

## Working setup

- Model key: `qwen/qwen3.8-27b` (variants q4_k_m default, q6_k; q4_k_m is 17.74 GB, vision + tool use). Loaded as identifier `belfast-vlm`.
- `lms.exe` is at `C:\Users\Yordi\.lmstudio\bin\lms.exe`.
- `lms server start --port 1234 --bind 127.0.0.1` (bind flag exists, default is localhost). `lms server stop` to stop.
- `lms load qwen/qwen3.8-27b --identifier belfast-vlm --context-length 16384 --gpu max --ttl 1800 -y`
- `lms load qwen/qwen3.8-27b --estimate-only --context-length 16384 --gpu max` -> Estimated GPU memory 20.21 GiB, total 20.21 GiB.
- `lms unload --all`; `lms ps --json` lists loaded models.
- API: `POST http://127.0.0.1:1234/v1/chat/completions` with `image_url` data URIs and `response_format: json_schema` (strict) works; the reply is schema-valid JSON.

## Reasoning effort: not controllable (observed)

The model always emits a `reasoning_content` block. Variants tried on the same frame (completion tokens / reasoning tokens):

| Request | Tokens | Reasoning tokens |
|---|---|---|
| `reasoning_effort: "low"` | 255 | 219 |
| `reasoning_effort: "medium"` | 285 | 239 |
| none | 352 | 302 |
| `chat_template_kwargs.enable_thinking=false` | 405 | 352 |
| `reasoning: {"effort":"high"}` | 335 | 299 |

Differences are noise (single samples); no parameter turned thinking off or scaled it. Over the 20-frame run low averaged 312 tokens, the 5 medium frames 276. Treat "low/medium" as a label only. `tools/vlm.py` still sends `reasoning_effort` (harmless). Not tried: `/no_think` prompt switch, a different chat template. Thinking costs about 250-300 tokens per frame on this simple task; harder scenes will think longer, so keep `max_tokens` generous (default 4096).

## Measurements

| Item | Value |
|---|---|
| VRAM baseline (nvidia-smi memory.used) | 1706 MiB |
| VRAM after load | 18921 MiB |
| VRAM peak during inference (0.5 s sampling) | 23962 MiB of 24564 (very tight; other apps' VRAM counts; keep the desktop light) |
| VRAM after unload | 1274 MiB (within baseline +300; it is below baseline) |
| System RAM used before / after load / during / after unload | 13262 / 30622 / 28969 / 7391 MiB (noisy: other apps; load briefly pushes RAM near the 31 GB limit) |
| Load time (server already up) | 24.1 s |
| Prompt tokens per 960x540 frame | about 600 |
| Seconds per frame, low | mean 11.1, min 7.8, max 19.3 (n=20) |
| Seconds per frame, medium | mean 9.6, min 9.0, max 10.9 (n=5) |
| Completion tokens per frame, low / medium | 312 / 276 mean; about 28 tokens/s |

## 20-frame test

20 synthetic 960x540 road scenes (1-4 white dashed lane lines, a coloured rectangle, a two-digit number). Schema: `lane_lines` integer, `dominant_colour` enum, `text_seen` string, `confidence` number.

- Low: 20/20 schema-valid on the first attempt (0 retries). Correct: lanes 20/20, colour 20/20, number 20/20.
- Medium (first 5 frames): 5/5 valid, all fields correct.
- Accuracy on toy images is informational only.

## Guard rails in the helper

- `load` refuses while a BeamNG process is running, then takes `H:\BelfastData\gpu.lock` (atomic create, polls 15 s, default timeout 20 min) containing `vlm pid=<pid> t=<epoch>`. The lock is released only if the file still holds our own tag; `session()` always unloads and releases, even on error.
- During this spike the lock was held by T-005 (and BeamNG was running) for a few minutes; vlm waited and only loaded once both were clear.
- CLI `load` leaves our lock on disk until `unload`, which removes it only if it begins with `vlm `.

## Open points

- Peak VRAM of 23.96 GB leaves almost no headroom; a lower context (8192) or q4 KV cache would help if frames get larger or the desktop uses more VRAM.
- Reasoning control: ask whether the `/no_think` prompt switch or a newer LM Studio build exposes it.
