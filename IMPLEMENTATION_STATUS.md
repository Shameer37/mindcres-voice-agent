# Implementation status

## Latest source of truth — 2026-10-23

- [x] Browser call `72bc0869-8661-4146-bcc2-8bb5c7ecf8ca` analyzed from persisted transcript and metrics. “Fully restored” missed deterministic extraction, then took 5963.8 ms through Ollama; phrase mapping added. Ambiguous STT phrases remain unguessed; original audio was not retained.
- [x] Clear issue-resolution, still-facing, identity, score, goodbye, callback, and opt-out handling strengthened with regression tests. Standalone “Bye” ends without DNC or a fabricated rating.
- [x] Privacy-conscious audio/STT/extraction diagnostics and a bounded 4 MiB voice-specific TTS frame cache added. Sequential simulated calls measured repeated-prompt first audio at approximately 0.1–0.2 ms on cache hits; cold provider TTS remains variable.
- [x] Fresh actual WebSocket happy and unresolved calls passed, with expected results and terminal events. Current full suite: **165 passed**; compileall passed. Details and latency sample are in `EVAL.md`.
- [ ] Browser mic and audible playback remain unverified. Latest 12 simulated caller turns measured response median/p90 1657.3/2697.4 ms; the 1.5 s end-to-end target remains unmet.

- [x] Stable post-playback caller timers: 9 s initial/7 s subsequent defaults, one reminder, grace interval; armed only when playback completes in both cleanup paths.
- [x] Live WebSocket happy, unresolved, and barge-in scenarios passed with persisted results and terminal events; a live silent caller received one reminder and `no_input`. See the latest addendum in `EVAL.md` for call IDs and measured latency.
- [x] Regression validation: full suite **134 passed**; compileall passed; 21 termination/timer/persistence checks passed in each of 10 repeated runs.
- [ ] Physical browser microphone, audible speaker playback, acoustic barge-in, and ≤4-minute demo recording remain unverified. Follow README diagnostics; do not present synthesized WebSocket caller evidence as browser verification.
- [ ] Submission readiness: NOT READY until browser microphone/playback and demo are demonstrated; response median/p90 remain 1899.8/2766.1 ms against the 1.5 s target. Hindi/Telugu mappings have unit coverage but no real speech evaluation.

## Audio pipeline follow-up — 2026-10-22

- [x] Browser capture reliability changes: PASS — explicit microphone device picker; transmit underruns now send real μ-law silence (`0xFF`, previously loud `0x00`); 1,024-sample callbacks; continuous fractional-window downsampling preserves phase and boundary samples; μ-law encoding matches Python; one-second bounded queue records drops; device/settings, context state, RMS/peak, captured samples, transmitted frames, and near-silence are diagnosed. Inline JavaScript syntax and a 1,000-block Node resampler check passed (1,024,000 input samples -> 170,666 samples, expected 170,666).
- [x] Server media diagnostics: PASS — debug-level five-second aggregates track decoded payload bytes, complete 160-byte frames, audio duration, non-frame-sized payloads, and receive gaps over 40 ms; raw audio is not logged.
- [x] TTS frame integrity and timeout: PASS — partial μ-law frames are padded with `0xFF` silence (previously `0x00`, a loud value); provider and FFmpeg work have 12-second timeouts; malformed WebSocket TTS frames fail visibly. Server logs frame count, intended duration, and max pacing lag.
- [x] Known WAV through real WebSocket and STT: PASS — direct STT transcript matched the actual endpoint transcript exactly. 345 incoming frames, 160 bytes each; the agent correctly required identity because the recorded caller said Rahul while CUST001 is Aarav Sharma. Saved partial result and received caller-hangup terminal event (call `556fd07e-fa6b-4bdf-a172-9d7a05f03ba4`).
- [x] Real TTS WebSocket send: PASS — 431 outgoing media frames all decoded to 160 bytes, mark was acknowledged, terminal hangup received (call `a504a6de-783c-4d83-b658-f67657355527`). Median received frame interval was 31.44 ms, so the intended 20 ms cadence is not yet demonstrated. This test did not render browser audio.
- [ ] Live browser microphone and audible playback: NOT RUN — desktop helper failed initialization with `failed to write kernel assets: The system cannot find the path specified`. Manual steps and the diagnostics to inspect are in README.
- [x] Unresolved issue scenario: PASS with synthesized caller — reproduced the missing answer; the 2.64 s problem phrase had no VAD speech run over 240 ms, while the old barge-in gate required 280 ms uninterrupted and dropped audio during active playback. It now accumulates speech across ≤160 ms gaps. Rerun `ed4529a6-dde2-414b-aa2b-c0af118d4c3d` saved `still_facing="My internet keeps dropping."`, no unclear fields, and received terminal hangup. Physical browser microphone remains NOT RUN.
- [x] Barge-in regression scenario: PASS — rerun `6be04098-aff4-4b4b-85c8-d8c0dae0be41` sent an answer during playback, observed `clear`, saved expected feedback, and received terminal hangup. Interruption was measured at 2231 ms after playback start, after first audio at 1115 ms.
- [x] Browser playback diagnostics: IMPLEMENTED — DevTools now reports sampled decoded frame size/count, arrival gap, PCM RMS, AudioContext state, scheduled queue depth, active sources, and mark-drain/ack timing. This improves manual verification; audible browser playback itself remains NOT RUN.
- [x] Latest automated checks: PASS — after final changes, `.venv\Scripts\python.exe -m pytest -q` returned **122 passed in 2.43 s** (requires local socket access); compileall passed. The 115-test result is the earlier baseline.
- Readiness: NOT READY — physical browser microphone capture and audible playback remain unverified; measured TTS frame arrivals were slower than 20 ms. No demo video exists.

Source of truth: `C:\Users\shame\Desktop\task.pdf` (SkyFiber Take-Home Task, six pages). The pasted master prompt adds a detailed completion workflow; the original assignment controls functional requirements.

## Phase A — Audit

- [x] Read assignment requirements and inspect all six rendered pages: PASS — checked protocol, conversation, audio, timing, storage, simulator, tests, and deliverables.
- [x] Inspect project tree, logs, and architecture: PASS — FastAPI/SQLite, browser simulator, faster-whisper, Ollama, Edge TTS, ten fictional customers, 10 audio files, 22 test modules. `import_debug.log` showed model initialization taking ~170 seconds during app import.
- [x] Check Git state: NOT RUN — directory contains no `.git` repository.
- [x] Final test suite: PASS — latest `.venv\Scripts\python.exe -m compileall -q app scripts tests websocket_client.py websocket_audio_test.py` passed; full suite completed with 115 passed in 1.38 seconds. Focused termination/conversation tests passed 22/22 in each of 10 consecutive runs in the prior run.
- [x] Requirements-to-files map: PASS — protocol/state in `app/api/websocket.py` and `app/services/conversation_service.py`; schemas/storage in `app/schemas.py`, `app/models.py`, and `app/database.py`; browser in `web/index.html`; prompts in `prompts/`; tests in `tests/`; setup/evaluation in `README.md` and `EVAL.md`.

## Phase B — Implement

- [x] WebSocket event protocol and media compatibility: PASS — accepts assignment `event` and legacy `type`; playback marks have unique names and only matching client acknowledgements complete them.
- [x] Terminal call handling: PASS — result committed before close; terminal TTS keeps receiving mark acknowledgements; one `hangup` is sent before socket close; disconnects/send errors are handled; repeated `finish_call` is idempotent. Regressions passed 10 consecutive runs.
- [x] Barge-in handling: PASS — 280 ms onset frames retained; live run received audio before interruption, observed `clear`, recorded interruption 2327 ms after start and after first audio at 1238 ms, continued conversation, saved result, and received terminal event.
- [x] Conversation corrections, repeat requests, identity lock, retries, callbacks, and opt-outs: PASS — existing logic retained, repeat is not counted as a retry, abusive-call opt-out added.
- [x] Structured result validation: PASS — persisted terminal results pass `FeedbackResult`, with nullable identity for partial calls and explicit end reason/opt-out state.
- [x] STT loading and timeouts: PASS — lazy load, cache-only by default, explicit download/setup command, STT timeout.
- [x] REST, SQLite, and browser simulator: PARTIAL — live happy/barge-in results persisted; browser playback mark acknowledgement implemented, but browser microphone and recording not performed.
- [x] Provider diagnostics and safe config: PARTIAL — `.env.example`, provider diagnostic command, explicit STT setup; Ollama and Edge TTS diagnostics passed and STT inference ran in live calls. Edge TTS remains network-dependent and no offline voice is available here.
- [x] Simulated caller and asset manifest: PARTIAL — happy, barge-in, wrong-person, and latest unresolved scenario passed with synthesized caller audio; latest unresolved result saved the exact problem description. Earlier unresolved failures reproduced and led to the inter-word barge-in fix. Physical browser microphone remains NOT RUN.
- [x] Isolated audio evaluation: PASS — ten synthesized known utterances compared through the 8 kHz μ-law and 16 kHz PCM faster-whisper paths; normalized WER 0% for both. Offline detector replay produced one turn per utterance at 500 ms and 1000 ms. Live threshold comparison remains outstanding.

## Phase C — Test

- [x] Unit/audio/API/state checks: PASS — final `compileall` passed; full suite 115 passed in 1.38 seconds; earlier terminal-focused regressions passed 10 repeated runs.
- [x] Simulated-caller scenarios: PASS — happy, barge-in, wrong-person and the latest unresolved run passed; unresolved saved the expected `still_facing` text and terminal event.
- [x] STT and offline threshold measurements: PASS — `scripts/evaluate_audio_measurements.py` generated `data/stt_comparison.csv` for n=10 and replayed both detector thresholds. These do not substitute for live API threshold scenarios.
- [ ] Provider diagnostics and browser smoke test: PARTIAL — Ollama/Edge TTS diagnostics passed and STT ran live; browser microphone test/recording remain NOT RUN.

## Phase D/E — Submission and final audit

- [x] README, honest EVAL, Mermaid architecture, `.env.example`, `.gitignore`, manifest: PASS — generated or updated.
- [x] Audit secrets, assets, and run instructions: PARTIAL — `.env` remains ignored; `.env.example` has no credentials; fake customer data only; no model weights. Logs/audio retained, transcripts not claimed as verified.
- [ ] Submission-readiness: NOT READY — live browser microphone capture, audible browser playback, and demo recording remain unverified; TTS frame pacing and response latency remain above targets. The ten-utterance 8/16 kHz and offline 500/1000 ms detector comparisons are complete.

## Stabilization pass plan (2026-10-22)

1. Trace timeout initialization through greeting playback acknowledgements and inspect VAD/barge-in behavior against regression tests and available reference audio.
2. Make one minimal coordinated change for verified defects, adding deterministic regressions for greeting timing, isolated/background noise, and onset retention; preserve the existing 280 ms speech gate unless evidence supports another value.
3. Run focused tests, full pytest, compileall, and existing simulated caller scenarios where providers are available; update EVAL and status only with observed results.

## Stabilization pass results (2026-10-22)

- [x] Greeting timing cause and correction: the first unanswered turn used the same 6 s timeout as later questions, shorter than the requested initial window. Silence checks were already playback-gated and the timestamp reset after playback completion; the call-start timestamp itself was not the source of the early reminder. It is now explicitly unset at start and armed after the greeting task completes; defaults are 9 s initially and 7 s for later turns. One reminder remains enforced before final closure.
- [x] Noise evidence: energy-only VAD plus 280 ms cumulative barge-in persistence could accept sustained noise. Added an autocorrelation-based voiced-evidence check on 8 kHz PCM and applied it to turn, activity, and barge-in decisions. `SustainedSpeechGate` keeps candidate onset frames across bounded gaps and discards separated short bursts. Tests: 20/20 synthetic broadband-noise frames (RMS >500) rejected, voiced tone accepted, short bursts do not trigger, triggered onset retains its frame sequence. Real microphone noise/echo is still NOT RUN; periodic tonal noise may be accepted and unvoiced/noisy speech may be missed.
- [x] Available WAV boundary replay: 10 checked-in WAVs replayed after μ-law round trip. At 500/800/1000 ms, eight short assets each gave 1/1/1 turns; `reply_1.wav` 2/2/1; `test_speech.wav` 3/2/1. At the production 800 ms threshold, longer clips split at natural pauses. This warrants live timing review; no STT result was changed or inferred from VAD counts.
- [x] Focused regression checks: **13 passed** across VAD and new silence-watchdog policy tests. `compileall` passed.
- [x] Full suite and compile after final changes: **122 passed in 2.43 s**; `.venv\Scripts\python.exe -m compileall -q app scripts tests websocket_client.py websocket_audio_test.py` passed. Non-elevated API tests stalled in Windows asyncio `_fallback_socketpair`; local socket permission resolved it.
- [x] Current-code live WebSocket scenarios: happy `16051bd3-83d1-4d81-886d-aa8bcc7c484c` passed; unresolved `742e00ad-889f-4b59-9455-693b658c5fa2` passed and saved `still_facing="My internet keeps dropping."`; barge-in `2b34e4f1-90a3-49b1-9a7a-5dccae097410` passed with `clear`, persisted answers, and terminal hangup; no-response `d4434f2a-3503-4d37-a159-2d762d5e8105` delivered exactly one reminder and persisted `outcome=no_response` before terminal hangup. One earlier barge attempt timed out at Edge TTS; a subsequent retry passed after provider recovery.
- [ ] Physical browser microphone, actual ambient noise/echo barge-in, audible speaker playback, and demo recording remain NOT RUN. The current browser checks are manual only; no browser automation was available.
- Readiness: NOT READY to claim complete browser/acoustic verification. Highest-priority next action: complete README's manual browser checklist and record the ≤4-minute demo while checking mic diagnostics, playback stats/mark ACK, barge-in clear, unresolved answer, and saved call result. Inspect the long WAV's 800 ms splits during that run.

## STT experiment pass (2026-10-23)

- [x] Inspected actual STT model initialization, faster-whisper call parameters, μ-law/PCM/resample path, logs, environment, and tests.
- [x] Added reversible `STT_EXPERIMENT` variants: baseline (default), beam3, auto_language, condition_context, vad_filter, base_model. Baseline settings stay selected by default. No conversation, extraction, transport, VAD-before-STT, persistence, or TTS behavior was changed.
- [x] Added per-transcription debug diagnostics for experiment/model, input and model sample format/rate, duration, STT milliseconds, exact parameters, raw transcript, and segment times/log-probability/no-speech-probability where available.
- [x] Added `scripts/experiment_stt_configs.py` and `data/stt_experiment_results.csv`: 12 fixed-reference Edge TTS phrases shared across six configurations. Full-matrix result: baseline WER 9.72%, exact 83.3%, latency 509.5/575.0 ms median/p90; beam3 3.47%, 83.3%, 587.4/658.5 ms; auto-language 9.72%, 83.3%, 1116.1/1291.9 ms; context 9.72%, 83.3%, 583.5/651.2 ms; VAD 5.56%, 83.3%, 535.7/626.1 ms; base 1.39%, 91.7%, 1030.0/1125.1 ms. Dataset is clean, one-voice synthetic speech, not live callers.
- [x] Measured Windows process working set in the full sequential matrix (238.8 MB baseline through 376.0 MB at base; cumulative resident allocations); a fresh tiny-only run peaked at 239.2 MB. Fresh base-only memory rerun was blocked by Edge TTS disconnecting during phrase regeneration, so base per-model memory comparison remains unmeasured.
- [x] Focused STT regression tests: 8 passed. Final full suite after the environment-selector regression: `.venv\Scripts\python.exe -m pytest -q` — **169 passed in 3.12 s** (loopback-enabled run; sandbox run stalled at Windows asyncio socket setup). Final `compileall -q app scripts tests websocket_client.py websocket_audio_test.py` passed.
- [ ] No experiment is recommended as production default yet: the corpus is small/synthetic, repeated runs varied, no browser/accent/noise data. `STT_EXPERIMENT=baseline` remains the rollback/default.

## Answer-extraction follow-up (2026-10-23)

- [x] Audited saved call `724fdefe-c517-429d-b6ee-ea54c58260e8`: recognized “Yeah”, “No”, and “0” were mapped to correct fields. The “couldn't make that out” turns have no caller transcript; no call-specific audio was retained, so their capture/STT root cause remains unknown.
- [x] Fixed confirmed decimal-prefix bug: `1.3` / `one point three` can no longer become rating 1. Decimal NPS is rejected, NPS zero is preserved; invalid scores remain pending and trigger clarification.
- [x] Added contextual yes/yeah/yep/correct/“that's right” and no/nope parsing for identity, issue resolution, and technician-arrival confirmation. Ambiguous/empty identity text is not accepted.
- [x] “I have another feedback” now prompts for its content without saving the phrase or consuming a retry. Empty/unclear model extraction does not invent a field value.
- [x] Added LLM fallback diagnostics for reason, parser, normalized/original transcript (DEBUG-only), and elapsed time. Call evidence attributes ~5.95 s turns to the other-feedback extraction fallback; a timeout itself is not confirmed because the server exception log was not retained.
- [x] Focused extraction/state tests: **86 passed**. Full suite: `.venv\Scripts\python.exe -m pytest -q` — **204 passed in 3.01 s** (loopback-enabled). Compile check: `.venv\Scripts\python.exe -m compileall -q app scripts tests websocket_client.py websocket_audio_test.py` — passed.

## Latest call follow-up — 2026-10-23

- [x] Call `893ee507-15b2-4168-95c1-5ec6e10f695b` export reviewed. Clear additional-feedback intent was not covered by the previous exact phrase rule; this led to the generic invalid-answer path and unnecessary LLM fallback. Added patterns for “something else to add”, “I have more feedback”, and “I have another comment”, including equivalent short variants. These produce a request for actual feedback; intent is never persisted as content.
- [x] Ambiguous final feedback remained null and was marked unclear in the supplied saved result. No answer fabrication was observed. The transcript cannot adjudicate whether “and spread” represented the caller's intended feedback.
- [x] Inspected STT runtime selection and preprocessing. Baseline remains tiny/CPU/int8, beam size 1, no faster-whisper VAD filter unless `STT_EXPERIMENT` selects another option. The project `.env` has no `STT_*` overrides, but the historical call process did not persist its environment/config. The call record also has no raw audio, audio duration, startup snapshot, or stage logs; so the particular 14.81 s delay cannot be attributed to model load, long audio, lock wait, or inference. Added future startup config and per-utterance preprocessing, lock-wait, model-init, inference, and total timings. Baseline unchanged.
- [x] TTS path had provider and FFmpeg timeouts and frame-send logs, but did not distinguish first frame delivery from synthesis/conversion or record client playback completion. Added stage/category error logging without exception contents, frame-preparation/synthesis and first-frame delivery timings, and mark-ack wait timing. Existing “Speech generation failed” event remains explicit. Historical export does not retain the exception cause; Edge TTS vs conversion vs transport is unresolved. No browser playback run or retry/fallback added.
- [x] LLM extraction's old duration was end-to-end; no application queue exists in this extraction path. Added request/parse timings and Ollama server total/load/eval durations when supplied, plus timeout classification. Current call export cannot separate these stages retrospectively.
- [x] Preserved extraction of “Yes”, “utterly resolved”, “No”, “would rate 4”, and “around 8”. No VAD, barge-in, timer, protocol, or finalization changes.
- [x] Focused test set: **100 passed**. Full suite: `.venv\Scripts\python.exe -m pytest -q` — **211 passed in 2.34 s**. Compile: `.venv\Scripts\python.exe -m compileall -q app scripts tests websocket_client.py websocket_audio_test.py` — passed.
- [ ] No new browser test was run. Live microphone accuracy, audible TTS, and whether the historical call's STT experimental variant was active remain unverified. See detailed caveats and metrics in `EVAL.md`.

