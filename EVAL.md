# SkyFiber Voice Agent Evaluation

## Evidence policy

The previous version of this report contained latency values, accuracy claims, and scenario passes without reproducible logs or scripts. Those claims were removed. This report records only checks run in this workspace; it does not infer end-to-end success from mocks or source review.

## Environment and checks

- Date: 2026-10-22 (Asia/Calcutta)
- OS/runtime: Windows; project virtual environment reports Python 3.12.10 and pytest 9.1.1.
- Project models configured in code: faster-whisper `tiny` on CPU/int8, Ollama `qwen2.5:3b`, Edge TTS `en-US-AriaNeural`.
- CPU/GPU/RAM: NOT MEASURED. Ollama `qwen2.5:3b` and Edge TTS were reachable during diagnostics; faster-whisper tiny cache was present and live inference succeeded in simulated calls.
- Previous full-suite baseline: **115 passed in 1.65 seconds** before the current stabilization changes. In this pass, focused regressions returned **13 passed** and `compileall` passed. The attempted post-change full suite stalled before the first API test while creating a Windows asyncio socket pair; details are recorded below.
- Provider diagnostic: Ollama READY; Edge TTS READY (153 160-byte frames); STT cache file found. Diagnostic did not load STT, but live calls did.
- No real microphone/browser call or screen recording was completed. The desktop automation helper failed initialization twice with `failed to write kernel assets: The system cannot find the path specified`, so browser capture was unavailable in this session.

## Caller-turn latency

**Live simulated-call sample — 15 caller turns** from one happy call, one unresolved attempt that failed to save its ongoing-problem description, and one pre-optimization barge-in call. Values are server-side `turn_metrics`; p90 is nearest-rank. This sample includes an incomplete scenario and pre-optimization runs.

| Call | Turn | STT ms | TTS first ms | Response ms |
|---|---:|---:|---:|---:|
| happy f36ff9c4 | 3 | 802.6 | 1914.0 | 18629.0 |
| happy f36ff9c4 | 5 | 767.7 | 2040.9 | 8959.0 |
| happy f36ff9c4 | 8 | 689.6 | 1361.1 | 7477.1 |
| happy f36ff9c4 | 10 | 713.6 | 1655.0 | 6893.8 |
| happy f36ff9c4 | 13 | 653.4 | 1818.1 | 2507.1 |
| unresolved bf1316b4 | 3 | 621.1 | 1741.3 | 7742.2 |
| unresolved bf1316b4 | 6 | 715.0 | 1884.3 | 8442.3 |
| unresolved bf1316b4 | 9 | 795.4 | 1773.9 | 22964.2 |
| unresolved bf1316b4 | 12 | 721.1 | 2056.3 | 23359.8 |
| unresolved bf1316b4 | 15 | 878.7 | 2133.2 | 8774.0 |
| unresolved bf1316b4 | 18 | 753.3 | 1255.9 | 2044.8 |
| barge-in 82515e8a | 5 | 650.2 | 1140.8 | 8775.5 |
| barge-in 82515e8a | 7 | 498.8 | 1167.1 | 7880.0 |
| barge-in 82515e8a | 9 | 473.6 | 1418.2 | 8388.0 |
| barge-in 82515e8a | 11 | 457.4 | 1352.5 | 1847.8 |

For these 15 turns, response median was **8388.0 ms** and nearest-rank p90 **22964.2 ms**. STT was mostly under 0.9 s; two unresolved turns used about 10.8 s in extraction. Local LLM reply timeouts added up to 15 s. TTS first audio was generally 1.3–2.1 s.

## Latest browser-call investigation — 2026-10-23

Call `893ee507-15b2-4168-95c1-5ec6e10f695b` was inspected from its supplied persisted export. This is evidence from the call record, not a new browser run.

- Additional feedback: the deterministic intent rule recognized only “I have another feedback”. It did not cover “something else to add”, “I have more feedback”, or “I have another comment”, so that clear intent reached the generic invalid-answer path and the LLM fallback. Added exact intent patterns for these variants. They ask for content without saving the intent phrase or consuming a retry. The next caller response still must supply actual content; after configured invalid attempts, the existing unclear-field finalization applies.
- Saved call fields show the later phrase “and spread” was not persisted as substantive feedback: `other_feedback=null`, `unclear_fields=["other_feedback"]`, `needs_followup=false`. Thus the recorded final result did not fabricate feedback. The transcript alone cannot determine whether “and spread” was a clipped meaningful comment or an STT error.
- STT metrics in the supplied export include 4043.43, 5152.85, and 14808.17 ms. The record has no original audio, utterance byte/duration values, model-selection snapshot, per-stage timing, or contemporaneous server log. No original audio file for this call was available. Source defaults are tiny/CPU/int8 and `STT_EXPERIMENT=baseline`; no `STT_*` entries are in the project `.env`, but the historical server process environment was not captured. Therefore accidental experimental activation, cold load, long audio, lock wait, resampling cost, and model inference cannot be distinguished for this call. No STT default was changed. New logs report selected configuration, audio duration, preprocessing, lock wait, first model initialization, and inference separately for future runs.
- TTS first-audio values include 3199.56–10368.59 ms on turns with data; failed turns have null TTS/response metrics. Existing code measured first frame only after complete synthesis and conversion, so the old number does not isolate provider, conversion, WebSocket delivery, or browser playback. The export's “Speech generation failed” text confirms a user-visible failure but contains no exception detail; it cannot establish Edge TTS networking versus FFmpeg conversion or socket delivery. Added safe stage/category logs (without provider exception text), synthesis/frame-preparation timing, first-frame delivery timing, and browser mark-ack timing. No network retry or offline voice was added; no browser audio test was run.
- LLM fallback metrics around 6498–6896 ms are end-to-end extraction durations in the record. It contains no Ollama server timing or queue data. Current extraction has no explicit application queue; new diagnostics record client request/parse duration and Ollama total/load/eval durations when the provider returns them, with timeout categorized separately. Deterministic extraction remains preferred for clear answers; the intent phrases above now bypass Ollama.
- Confirmed preserved sample answers: “Yes” => identity confirmed, “utterly resolved” => resolved, “No” => no current issue, “would rate 4” => 4, and “around 8” => NPS 8. The answer validation path was not otherwise changed in this pass.
- Validation after this pass: focused feedback/STT/TTS tests **100 passed**; full `.venv\Scripts\python.exe -m pytest -q` **211 passed in 2.34 s**; `.venv\Scripts\python.exe -m compileall -q app scripts tests websocket_client.py websocket_audio_test.py` passed. These are automated tests, not browser evidence.

After the deterministic fast path and six-second local reply timeout, verified call `c1ea16b6` recorded five turns: response median **1726.7 ms**, p90 **2164.6 ms**; STT median 476.7 ms; TTS first-audio median 1354.4 ms. The 1.5 s target remains unmet.

## Scenario results

**Live scenario evidence** below comes from real Edge TTS caller audio, WebSocket media, SQLite/API results, and server terminal events.

| Scenario | Expected result | Observed result / evidence | Status |
|---|---|---|---|
| Happy path | Completed with validated feedback | Current-code synthesized-caller call `16051bd3-83d1-4d81-886d-aa8bcc7c484c`: all expected answers saved; `completed`; terminal event received. Several turns also received one silence reminder while the scripted answer was pending. | PASS (simulated caller; browser mic NOT RUN) |
| Unresolved issue | Follow-up required and problem description saved | Current-code call `742e00ad-889f-4b59-9455-693b658c5fa2` transcribed “My internet keeps dropping.” and persisted exact `still_facing`, no unclear fields, `needs_followup=true`; terminal hangup received. | PASS (simulated caller; browser mic NOT RUN) |
| Barge-in | Interrupt agent audio; clear; process caller response | Current-code call `2b34e4f1-90a3-49b1-9a7a-5dccae097410`: `clear` observed, interruption at 2344.6 ms after playback began and after first audio at 1249.0 ms; expected answers persisted and terminal event received. An earlier attempt hit a 12 s Edge TTS timeout, and its immediate retry failed Edge TTS DNS before connecting; the later full run passed. | PASS (simulated caller; browser mic NOT RUN) |
| Silence | One reminder, then no-response closure | Current-code WebSocket call `d4434f2a-3503-4d37-a159-2d762d5e8105`: one reminder at 20.1 s from start (after greeting playback plus configured 9 s), final goodbye at 44.2 s, terminal `no_input` at 51.2 s; persisted `outcome=no_response` and null answers. | PASS (live WebSocket; no caller audio) |
| Wrong person | Close before collecting feedback | `2022e0cd`: `wrong_person`, identity false, no feedback, terminal event received. | PASS |
| Invalid rating/NPS | Retry twice, then null plus `unclear_fields` | Not run | NOT RUN |
| Answer correction | Later valid answer replaces earlier answer | Unit test covers the state service only; no live call | PARTIAL |
| Callback | Callback request/time persisted and call ended | State-service unit test exists; no live call | PARTIAL |
| Stop calling | Opt-out persisted | State-service unit test exists; no API persistence run | PARTIAL |
| Disconnect/hangup | Partial result and transcript persisted | Not run against a live WebSocket | NOT RUN |

The script supports `happy`, `unresolved`, `barge-in`, and `wrong-person`. It uses Edge TTS caller speech, sends paced 20 ms μ-law frames to the real WebSocket, correlates marks to bot transcript sequence, and reads the persisted result. The latest unresolved scenario passes after the barge-in gap fix; earlier failing runs remain recorded as historical failures.

## End-of-turn threshold comparison

**Offline turn-detector replay completed; live API comparison not run.** `scripts/evaluate_audio_measurements.py` replayed the same ten Edge TTS telephone utterances through `EnergyVAD`/`TurnDetector`. Each utterance produced one detected turn at both 500 ms and 1000 ms (10/10 each; no split in this small sample). This is not evidence of live call latency or caller cut-off rates; the production default remains 800 ms.

## 8 kHz versus 16 kHz STT

**Completed on 10 Edge TTS reference utterances.** For each phrase, FFmpeg decoded the same source to mono 16 kHz PCM. The 8 kHz arm polyphase-resampled to 8 kHz, G.711 μ-law encoded, then used `STTService.transcribe_ulaw` (μ-law decode and 8→16 kHz resampling); the 16 kHz arm sent the original 16 kHz PCM directly to the same cached `faster-whisper tiny` CPU/int8 model. With case/punctuation normalization and numeric words/digits normalized, mean WER was **0.0% at both rates** (10/10 exact normalized transcripts). Full references/transcripts are in [data/stt_comparison.csv](data/stt_comparison.csv). This isolated test is separate from the synthesized live unresolved conversation recorded earlier; that scenario was not rerun after the current VAD change. No incorrect physical-microphone transcript was captured in this pass, so a live STT root cause is not established.

## Paid API cost scenario

No paid API is required by this implementation. No current prices were verified, so the following is only a formula, not a quote:

- 5,000 calls/day × 2.5 minutes = 12,500 call-minutes/day.
- Monthly cost = `12,500 × 30 × (verified STT price per minute)` + `5,000 × 30 × (verified LLM tokens/call × price/token + TTS characters/call × price/character)` + hosting and telephony.
- Provider, region, token/character volume, taxes, retries, and telephony rates are unspecified. No dollar estimate is asserted.

## Three concrete failure examples

1. **Terminal event race — fixed.** The original handler sent `hangup` and closed immediately after the final TTS mark, without waiting for the browser's playback acknowledgement. The simulator's reader paused to send that acknowledgement; the remote close could interrupt the reader before it consumed `hangup`. The server also labeled its mark acknowledgement as `event:"mark"`, which clients could interpret as another playback mark. Marks now have unique names, the server does not echo them as playback marks, and terminal playback drains matching acknowledgements before sending `hangup` and closing. Directly awaiting the playback task in the receiver caused a reproduced self-deadlock; the receiver-pump helper fixes that. Focused terminal tests passed 10 consecutive runs; live happy, wrong-person, and barge-in clients received the terminal event.
2. **Slow routine turns — mitigated.** Pre-optimization barge-in response median/p90 were 8134/8775 ms because straightforward answers incurred local LLM wording. Clear, validated answers now use the state-machine question; ambiguous wording still goes through the LLM. Reply timeout is capped at 6 seconds. The new live barge-in median/p90 are 1727/2165 ms; the 1.5 s target remains unmet.
3. **Ongoing issue phrase dropped during playback — fixed for the reproduced path.** The exact Edge TTS phrase was 2.64 s; direct STT recognized it. At the configured VAD threshold it formed speech runs of 10, 7, 12, 6, 9, and 11 frames, with intervening low-energy gaps; no voiced run exceeded 240 ms. During bot playback the old barge-in counter reset on every single quiet 20 ms frame and discarded all candidate frames until 280 ms of uninterrupted speech, so the phrase could not interrupt playback or enter the utterance buffer. The gate now accumulates speech across gaps up to 160 ms, retains the onset plus short gaps, and still requires 280 ms total speech before `clear`. The real unresolved simulated-caller call now passes and saves `still_facing="My internet keeps dropping."`. Browser microphone reproduction remains NOT RUN.

## Reproduction and limitations

1. `.venv\Scripts\python.exe -m compileall -q app scripts tests websocket_client.py websocket_audio_test.py` — passed before the audio follow-up; final verification is recorded below. The prior full suite was 110 passed in 1.58 seconds.
2. `python scripts/prepare_demo.py` — explicitly fetches Whisper weights if absent and imports fictional customers.
3. Start Ollama with `qwen2.5:3b`, then start FastAPI using the README command.
4. `python scripts/diagnose_providers.py` checks the local STT model, Ollama, and live Edge TTS reachability.
5. Run `python scripts/simulated_caller.py --scenario happy`, `unresolved`, and `barge-in`. Latest results: all three passed; unresolved persisted the exact ongoing-problem description.

Live browser microphone and browser recording were not completed. The live 500/1000 ms comparison remains outstanding; the 8/16 kHz ten-utterance reference comparison is complete. Ongoing issue capture remains an implementation blocker.

## Microphone and TTS integration follow-up (2026-10-22)

### Verified defects and changes

- Browser capture had a more direct verified defect: when its transmit queue was empty it sent `new Uint8Array(160)`, i.e. 160 bytes of `0x00`. In μ-law, `0x00` decodes to approximately −32124 PCM, while silence is `0xFF`; an underrun therefore injected a loud full-scale burst that could trigger VAD or playback noise. The fallback now sends 160 bytes of `0xFF`, with a codec regression confirming the difference. Capture also used 4,096-sample blocks (about 85 ms at 48 kHz), restarted the downsampler at each block, and floored each block's output count: 682 samples instead of fractional 682.67, about 0.1% sample loss and a reset averaging window at each callback. It had no queue bound or dropped-frame counter. It now uses a microphone device picker, 1,024-sample blocks, continuous fractional-window downsampling with carried phase/samples, Python-compatible μ-law negative-sample correction, a one-second bounded queue, silence/context warnings, and sampled device/RMS/peak/sample/frame/drop diagnostics.
- `split_ulaw_frames(..., pad_final=True)` previously filled a partial TTS frame with `0x00`. `0x00` is a loud μ-law value; silence is `0xFF`. Final-frame padding now uses `0xFF`, covered by decoding padded samples and asserting zero PCM. TTS provider and FFmpeg conversion now have a 12-second default deadline and report a clear timeout. WebSocket TTS rejects malformed frames and logs frame count, audio duration, and maximum pacing lag.
- The queue-underrun μ-law encoding and final-frame padding are verified root causes of loud non-speech audio; the downsampling issue is a verified sample loss/latency defect. Separately, the reproduced unresolved-issue failure was caused by the playback barge-in gate discarding phrase speech split into voiced runs shorter than its 280 ms uninterrupted threshold. The updated cumulative gate passes the real synthesized-caller scenario. Browser microphone reproduction remains NOT RUN.

### Reference audio through the live WebSocket

The real application was started on loopback. Direct local STT on `data/test_speech.wav` (16 kHz PCM, 6.895 s) returned `Hello, I am Rahul. The technician fixed my internet problem, and my connection is working fine now.` The same WAV was resampled to 8 kHz PCM, μ-law encoded, split into 345 paced 160-byte frames, and streamed through the actual WebSocket. The server transcript matched the direct transcript exactly. The selected customer was Aarav Sharma, so the agent correctly asked for identity confirmation rather than accepting feedback. The saved result for call `556fd07e-fa6b-4bdf-a172-9d7a05f03ba4` was `partial`, `needs_followup=true`, `end_reason=caller_hangup`; the client received `hangup`. Server metrics for its caller turn: STT **1318 ms**, LLM **6553 ms**, TTS first audio **1501 ms**, response-to-first-audio **9444 ms**. This is a real WebSocket/reference-WAV integration, not microphone or browser testing.

### TTS over WebSocket

On call `a504a6de-783c-4d83-b658-f67657355527`, the real endpoint emitted **431 media frames**, all exactly **160 bytes**. The test client received and acknowledged the playback mark and received terminal `hangup`. Median frame-arrival interval was **31.44 ms** in this run, above the intended 20 ms cadence; server instrumentation now records maximum send lateness for further measurements. This verifies server synthesis/codec/transport and client mark protocol. The test client did not render audio, so browser speaker audibility remains NOT RUN. No local TTS package or executable was installed; Edge TTS remains network dependent, with an explicit timeout and error rather than an empty successful response.

### Browser and remaining limitations

- Live browser microphone capture and audible browser playback: **NOT RUN**. `mcp__cua_repl` failed initialization with `failed to write kernel assets: The system cannot find the path specified`. Exact manual steps are in README under “Live verification status and manual recording”.
- Browser now exposes bounded playback diagnostics in DevTools: received/decoded bytes, non-160-byte frame count, median arrival gap, decoded PCM RMS, AudioContext state, scheduled queue duration, active source count, and mark drain/ack. These diagnostics have not yet been observed in an actual browser session.
- The synthesized unresolved caller scenario is **PASS** as recorded above; physical browser microphone reproduction remains NOT RUN.
- Browser screen/audio recording: **NOT RUN**. No demo video exists.

## Earlier unresolved and barge-in runs (before the stabilization pass)

That earlier unresolved scenario passed on call `ed4529a6-dde2-414b-aa2b-c0af118d4c3d`: the live simulated caller transmitted speech, server STT transcript included `My internet keeps dropping.`, conversation state advanced past `still_facing`, persistence saved that exact answer, `unclear_fields` was empty, `needs_followup=true`, and the client received terminal `hangup`. The current-code rerun is recorded in the scenario table and stabilization results below.

The real barge-in scenario was rerun after the change and passed on call `6be04098-aff4-4b4b-85c8-d8c0dae0be41`. The client sent the follow-up during playback, observed `clear`, the server saved the expected feedback and the terminal event. Its first interruption metric was 2230.8 ms after playback start; the turn's first audio was at 1114.6 ms. Result was `completed` with `unclear_fields=[]`.

The earlier unresolved run's seven measured caller turns had response median **1826 ms**, p90 **3070 ms**; STT median **490 ms**, p90 **1147 ms**; TTS first-audio median **1198 ms**, p90 **2569 ms**. The outlier turn asked about the ongoing issue. That barge-in run's five response latencies had median **1698 ms**, p90 **1741 ms**. The 1.5 s target remains unmet. These are server metrics from synthesized caller audio, not browser microphone or audible playback measurements.

### Previous audio-follow-up regression checks (superseded by stabilization results below)

After the follow-up changes, `.venv\Scripts\python.exe -m compileall -q app scripts tests websocket_client.py websocket_audio_test.py` passed and `.venv\Scripts\python.exe -m pytest -q` returned **115 passed in 1.38 seconds**. Node parsed the inline browser script, processed 1,000 consecutive 1,024-sample blocks at 48 kHz into exactly 170,666 samples at 8 kHz, and checked the browser's 160-byte `0xFF` underrun frame. These checks do not substitute for microphone or audible browser testing.

## Stabilization pass (2026-10-22)

### Greeting and unanswered-turn timing

The prior watchdog applied the same 6-second timeout to the greeting's first response and later questions. The playback gate already prevented silence checks during greeting playback, and the timestamp reset after playback; the start timestamp was not itself the early-reminder cause. The initial response window is configurable with `INITIAL_RESPONSE_TIMEOUT_SEC` (default 9 s); later questions use `NO_INPUT_TIMEOUT_SEC` (default 7 s). The timer is explicitly unarmed at start and is set after greeting task completion. The watchdog gate prevents checks before prompt completion, during playback, or while an utterance is active. Its action permits one reminder, then the existing no-response close after the next 2× response window. Added deterministic tests cover timer arming, both windows, and reminder/hangup edges.

### Background noise gate

The code's prior production gate accepted any frame above RMS 500, then accumulated 280 ms of energy across gaps up to 160 ms. Synthetic sustained broadband noise above the threshold therefore satisfied the same code predicate as speech; this demonstrates a false-barge vulnerability, but the user's actual physical noise source was not captured or diagnosed. The gate now additionally requires normalized autocorrelation above 0.25 within 40–160 sample lags (50–200 Hz at 8 kHz), and that same speech-like predicate is used for turn detection and meaningful-activity timing. Synthetic 20 ms frames verified that 20/20 broadband-noise frames at RMS above 500 are rejected and a voiced 120 Hz frame is accepted. This is a conservative voiced-speech proxy, not a trained VAD; tonal noise, acoustic echo, and noisy/unvoiced speech still require real-browser evaluation.

I replayed the ten checked-in WAV assets through the same 8 kHz μ-law encoding/decoding and speech-like VAD using `.venv\Scripts\python.exe scripts/evaluate_vad_assets.py`. Turn counts at 500/800/1000 ms silence thresholds were: `10.wav`, `5.wav`, `9.wav`, `no.wav`, `rating.wav`, `reply.wav`, `yes.wav`, and `yes_1.wav`: 1/1/1 each; `reply_1.wav`: 2/2/1; `test_speech.wav`: 3/2/1. This is offline boundary evidence only, not an STT accuracy result or a live microphone run. At the configured 800 ms threshold, the longer WAVs split at natural pauses; callers should use a full thought per turn, and the threshold remains an open tuning point.

The classifier ran 1,000 in-memory frames in **185 ms** on this host (0.185 ms/frame, approximately 0.9% of the corresponding 20 ms audio duration). This microbenchmark excludes receive/STT/LLM/TTS and is not an end-to-end latency measurement.

## Latest verification addendum (2026-10-23)

This addendum supersedes older scenario and test counts above where they conflict. These were actual WebSocket integration runs using the running application, Edge TTS synthesized caller speech, the local STT path, persisted SQLite results, and received terminal events. They are not physical browser microphone or speaker tests.

- Happy call `716fed3a-bd34-409f-a767-b165ca6f3835`: PASS. Identity confirmed; rating 5, NPS 9, no further issue, all expected fields persisted; terminal `completed`; no reminders.
- Unresolved call `337f8c5c-9a5a-4442-a8de-6faa5b978242`: PASS. Transcript contained “My internet keeps dropping.”; persisted `issue_resolved=no`, `still_facing="My internet keeps dropping."`, `needs_followup=true`; terminal `hangup` with follow-up; no reminders.
- Barge-in call `8edab79e-1bf9-4c5a-a3fb-3c3dec50853a`: PASS. Client observed `clear`, interrupted and resumed the caller turn, persisted expected answers, and received terminal `completed`; no reminders. First audio was 1407.1 ms after response start; interruption was 2503.6 ms after response start.
- Silence call `fd617506-669f-407e-bed8-ed5ed042a4af`: PASS. No caller media; exactly one reminder, final goodbye, terminal `no_input`, persisted `outcome=no_response` and null answers.

Across the 17 caller turns in the three complete scenarios, latency was: STT median **456.0 ms**, p90 **497.2 ms** (range 443.2–1034.1); extraction/state LLM median **0.2 ms**, p90 **0.4 ms** (3.2 ms max); TTS first audio median **1407.1 ms**, p90 **2260.7 ms** (range 1062.1–3355.5); end-to-end response median **1899.8 ms**, p90 **2766.1 ms** (range 1551.3–3848.2). These runs have direct, deterministic state-machine replies for routine validated answers, so their near-zero LLM time is not representative of ambiguous utterances requiring local LLM extraction. Response latency still exceeds the 1.5 s target at both median and p90. Compared with the earlier known regression call (LLM 3544.6 ms median/18709.7 ms p90; response 9847.1/29298.7 ms), this is an indicative, non-paired improvement; scenario and workload differ.

The timer regression root cause was two inconsistent playback-completion paths: one re-armed the caller timeout when playback ended, while the media-frame cleanup path cleared the playback task without re-arming it. Since the prompt timestamp was set before TTS generation, the timeout could count generation/playback as caller silence. The timer is now disarmed during generation/playback and armed at playback completion in both paths. Diagnostics include playback mark, state/turn, timer start/expiry and VAD speech transitions. A previously incorrect timeout outcome is also corrected: any saved structured answer prevents `no_response`; unresolved saved issue details yield `partial`/follow-up when applicable.

Latency work avoided routine LLM reply-generation calls and capped the local extraction timeout; measured TTS remains the largest median component and response p90 remains above target. No provider price was verified; the paid-API estimate remains a formula only. Browser microphone capture, audible playback, physical acoustic barge-in, Hindi/Telugu speech accuracy, and demo video remain NOT RUN. Hindi/Telugu prompt/voice mappings have unit coverage only.

Final source validation after the code changes: `.venv\Scripts\python.exe -m pytest -q` — **134 passed in 1.78 s**; `.venv\Scripts\python.exe -m compileall -q app scripts tests websocket_client.py websocket_audio_test.py` — passed. Repeated termination/timer/persistence regressions passed 21 tests on each of 10 consecutive runs. The full suite requires local loopback permission in this Windows environment.

### Validation for this pass

- `.venv\Scripts\python.exe -m pytest -q tests/test_vad.py tests/test_websocket_protocol.py::test_response_windows_and_single_reminder_policy tests/test_websocket_protocol.py::test_silence_watchdog_arms_only_after_greeting_playback_finishes` — **13 passed**. Coverage includes noise rejection, sustained-speech threshold, inter-word gaps, onset-frame retention, watchdog timing, and single-reminder policy.
- `.venv\Scripts\python.exe -m compileall -q app scripts tests websocket_client.py websocket_audio_test.py` — passed.
- Full `.venv\Scripts\python.exe -m pytest -q` with local socket access — **122 passed in 2.43 s** after the final code change. The non-elevated attempt stalled in Windows asyncio `_fallback_socketpair()`; the full suite required local loopback permission.
- `.venv\Scripts\python.exe -m compileall -q app scripts tests websocket_client.py websocket_audio_test.py` — passed after the final change.
- Actual current-code simulated-caller runs passed for `happy` (`16051bd3…`), `unresolved` (`742e00ad…`), and `barge-in` (`2b34e4f1…`). The unresolved answer and all scenario results were checked through persisted GET `/calls/{id}` results; all received terminal hangup. A separate no-response live WebSocket run got exactly one reminder, persisted `no_response`, and received terminal `hangup`.
- The current unresolved run's seven server turn metrics had response latency median **1771.6 ms**, nearest-rank p90 **3368.4 ms**; STT median **518.6 ms**, p90 **2074.7 ms**; TTS first-audio median **1171.9 ms**, p90 **1389.4 ms**. Cold first STT is an outlier; response remains above the 1.5 s target. The classifier microbenchmark is not included in these end-to-end values.
- Browser microphone, audible playback, and acoustic noise/echo scenarios remain NOT RUN. The 8 kHz/16 kHz WER table remains the existing n=10 synthesized-speech comparison (0% normalized WER on both paths); no physical-mic transcription was captured, so the reported live STT accuracy problem's root cause remains unconfirmed.

## Browser-call extraction and TTS latency follow-up (2026-10-23)

### Analyzed browser call

Call `72bc0869-8661-4146-bcc2-8bb5c7ecf8ca` is present in `data/skyfiber.db`, including its persisted transcript and 12 turn-metric rows; raw call audio was not retained. The database confirms:

- STT returned “fully restored” in about **590 ms**. The field-specific deterministic parser did not recognize that phrase, so it called Ollama; extraction took **5963.8 ms** and the answer remained null. Deterministic handling now maps clear “fully restored”, “it’s working now”, and “yes, completely” to `issue_resolved=yes`, while retaining negative/partial precedence.
- The turns “only with all of them” and “Please don’t” are ambiguous in the transcript; they remain unassigned rather than being guessed. Their LLM extraction durations were **5914.5 ms** and **6252.0 ms**. The later “No” following the still-facing prompt correctly saved `still_facing="none"`.
- “Bye” was treated as an invalid rating twice, consuming retries and leaving rating null/unclear. Standalone bye/goodbye now ends the session without setting opt-out and without inventing a rating. Explicit “stop calling” remains opt-out; callback remains separate.
- “I am the control” was not accepted as identity evidence because it is ambiguous. A later “Yes” confirmed identity. Natural clear forms such as “I am the account holder” and “this is the subscriber” are accepted.
- NPS `0` and “nothing else to add” were already correct. NPS extraction preserves zero as an integer and the result retains it.

The analyzed call’s 12 response rows measured: STT **603.9 ms median / 2604.9 ms p90** (3626.3 max); extraction/LLM **0.7 / 5963.8 ms** (6252.0 max); TTS first audio **1851.1 / 3040.0 ms** (4329.5 max); end-to-end response **2759.7 / 11897.9 ms** (12975.1 max). First-call model warm-up and the three Ollama extractions contribute to that run; the unusually high STT observations are preserved as measured, not attributed to a specific audio defect. The call audio is unavailable, so its microphone levels, boundary clipping, and Whisper input cannot be reconstructed.

### Current actual WebSocket measurements

After deterministic answer handling and bounded TTS frame caching, I ran the real app and simulated-caller client: cold-cache happy call `4dbd7b22-b8ae-412e-9fbe-fd9e4ee261cb` passed with all fields persisted and terminal `completed`; then unresolved call `397e6493-a314-45bc-8024-d8f0dcfb10a9` passed with `issue_resolved=no`, `still_facing="My internet keeps dropping."`, follow-up set, and terminal `needs_followup`.

Across these **12 caller turns** (five cold happy turns plus seven unresolved turns, five of which reused exact prompts warmed by the first call), measured STT was **477.3 ms median / 572.6 ms p90** (1103.1 max); extraction path **0.2 / 1.3 ms** (4.4 max); TTS first audio **1141.6 / 2216.3 ms** (2485.9 max); response **1657.3 / 2697.4 ms** (2986.9 max). The repeated identical prompts showed first-audio time dropping from 1256.4 to 0.2 ms for the initial feedback question, 1829.5 to 0.1 ms for rating, 2485.9 to 0.2 ms for NPS, 1047.4 to 0.2 ms for other feedback, and 1391.5 to 0.2 ms for the closing. These are actual cache-hit measurements in sequential simulated WebSocket calls, not browser playback timings. The warmed turn response still includes STT and send overhead.

This is not a paired comparison: the original call used browser capture and contained ambiguous turns; follow-up calls used synthesized speech with the real app. It demonstrates removal of the avoidable Ollama delay on clear answers and a cache benefit for repeated exact bot prompts. It does **not** establish improved cold-response p90: cold TTS/provider delays remain variable. The current response median is still above the 1.5 s target. TTS logs now separately report provider and FFmpeg conversion duration, without logging utterance content; WebSocket diagnostics record input duration/frame count/RMS/peak/cap status, configured STT language/model, transcript length/language confidence, and answer accept/clarify decision. The transcript remains in the call result as required.

Regression tests cover deterministic resolution/ongoing issue answers, spoken 1–5 ratings, NPS zero, bye versus opt-out/callback, and preserving valid feedback through a later TTS failure. Current browser microphone/audible-speaker validation remains NOT RUN. See README for manual browser checks; the original browser-call audio is unavailable for WER measurement.

Latest validation before the STT experiment: `.venv\Scripts\python.exe -m pytest -q` — **165 passed in 1.32 s** (loopback permission required); `.venv\Scripts\python.exe -m compileall -q app scripts tests websocket_client.py websocket_audio_test.py` — passed; application/STT/TTS service imports passed.

## Faster-whisper controlled STT experiment (2026-10-23)

The source baseline is tiny/CPU/int8; beam size 1; the caller's selected language; `vad_filter=False` (the configured 300 ms/150 ms VAD parameters are therefore inactive inside faster-whisper); `condition_on_previous_text=False`; `no_speech_threshold=0.6`, `log_prob_threshold=-1.0`, and `compression_ratio_threshold=2.4`. Browser audio is mono G.711 μ-law at 8 kHz in 160-byte/20 ms frames. The service decodes to 8 kHz PCM16, resamples to 16 kHz PCM16, then passes normalized float32 16 kHz samples to faster-whisper. The caller transcript is returned to the conversation and persisted; this experiment logs raw text at DEBUG and writes it only to the local evaluation CSV.

The opt-in `STT_EXPERIMENT` selector changes one setting at a time: `beam3`, `auto_language`, `condition_context`, `vad_filter`, or `base_model`. `baseline` remains the default. Installed faster-whisper was 1.2.1; these options are supported by its `transcribe` signature. The base weights were already cached; no model download was performed.

The repeatable harness generated a single set of 12 English phrases with Edge TTS, converted each to 16 kHz mono PCM with FFmpeg, then through 16→8 kHz resampling and μ-law encode. The exact same resulting μ-law clip per phrase was passed to each configuration (same order); language was `en` except in the auto-detection arm. This is explicitly simulated speech from one synthesized voice, with clean audio and no accent/noise diversity; it is not real caller audio or a browser test. WER and exact match are case/punctuation insensitive and normalize English number words to digits. `meaning_preserved` uses a small phrase-specific keyword check of each reference intent (e.g. resolved vs not resolved; required rating numeral), not a general semantic evaluator. Raw per-utterance references, transcripts, WER, exact match, phrase-intent result, STT ms, CPU process time, language confidence, and working set are in [data/stt_experiment_results.csv](data/stt_experiment_results.csv). The summary metrics below are from the run recorded in that file:

| Variant | Model | Mean WER | Exact match | Intent preserved | STT median / p90 | CPU process-time median | Max working set* |
|---|---|---:|---:|---:|---:|---:|
| baseline | tiny | 9.72% | 83.3% | 83.3% | 509.5 / 575.0 ms | 2148.4 ms | 238.8 MB |
| beam3 | tiny | 3.47% | 83.3% | 91.7% | 587.4 / 658.5 ms | 2375.0 ms | 252.4 MB |
| auto_language | tiny | 9.72% | 83.3% | 83.3% | 1116.1 / 1291.9 ms | 4546.9 ms | 264.2 MB |
| condition_context | tiny | 9.72% | 83.3% | 83.3% | 583.5 / 651.2 ms | 2312.5 ms | 279.2 MB |
| vad_filter | tiny | 5.56% | 83.3% | 91.7% | 535.7 / 626.1 ms | 2171.9 ms | 310.9 MB |
| base_model | base | 1.39% | 91.7% | 91.7% | 1030.0 / 1125.1 ms | 4187.5 ms | 376.0 MB |

*Working-set values come from one Windows process that ran variants sequentially; model/native allocations may remain resident between variants, so they are cumulative-process peaks, not isolated model memory. A fresh tiny-only run measured 239.2 MB peak across 12 phrases ([separate run data](data/stt_memory_baseline.csv)). An isolated fresh base-only memory run was attempted, but Edge TTS disconnected while regenerating the phrases, so a directly comparable per-model memory figure is NOT MEASURED. CPU process time can exceed wall STT time because CTranslate2 uses multiple threads. The full matrix is not a repeated randomized trial, and each audio source was generated once for that run.

Beam 3 and base reduced measured WER in the full matrix, with base roughly twice the STT latency. Repeated runs produced different WER: clean edge-generated phrases varied (for example, “Fully resolved” was decoded both correctly and as “Foley Result”), so this small sample is not sufficient evidence to change production defaults. Error examples in the recorded full matrix include “Fully resolved” heard as “Foley Result” by baseline/auto-language/context variants, and “four” rendered as “for” across every arm; do not treat numeral rendering (e.g. `8` for “eight”) as an error under this normalized metric. Auto language approximately doubled latency without accuracy gain here. Context conditioning between independent per-utterance calls has no meaningful caller-turn context and showed no improvement. faster-whisper VAD did not improve exact-match rate on this particular full-matrix run.

No preprocessing, answer extraction, call flow, or production default was changed. The configuration and diagnostics add tests only around STT selection/output. Browser/live-microphone accuracy, accented speech, background noise, and meaningful longer-turn context remain untested. To restore baseline explicitly use `STT_EXPERIMENT=baseline` (also the default). Reproduce with `.venv\Scripts\python.exe scripts\experiment_stt_configs.py`.

Final regression after the STT changes: focused `tests\test_stt_service.py` — **8 passed**; `.venv\Scripts\python.exe -m pytest -q` — **169 passed in 3.12 s**. The non-escalated run stalled at Windows asyncio socket setup; the loopback-enabled run completed. `.venv\Scripts\python.exe -m compileall -q app scripts tests websocket_client.py websocket_audio_test.py` — passed.

## Latest call answer-extraction investigation (call `724fdefe-c517-429d-b6ee-ea54c58260e8`)

The supplied saved call export contains transcript and turn metrics but no raw caller audio. The `recordings` directory has no files, there is no call-ID audio file under `data`, and the call/transcript schema stores recognized text, not captured audio. Therefore the turns that received “Sorry, I couldn't make that out” cannot be distinguished between empty/failed STT and an upstream capture/buffering issue from this artifact. Those turns have no caller transcript rows. The saved utterances “Yeah”, “No”, and “0” did proceed to identity confirmation, `issue_resolved=no`, technician punctuality false, and `nps=0`, respectively. This is evidence that the recognized affirmative/negative tokens reached the answer parser correctly; it is not proof that the unrecorded utterances were transcribed accurately.

Confirmed answer-extraction bug: the simple rating parser extracted the integer prefix from `1.3` and accepted rating 1, allowing the questionnaire to advance. The fast numeric parser likewise searched for a spoken score token. Decimal scores (including spoken “one point three”) are now rejected before score extraction; the short numeric validation path asks the caller again without an LLM call. NPS zero remains valid; decimal NPS scores are rejected.

The call's final two caller turns were “Force, experience, and war.” and “I have another feedback.” Both entered LLM extraction because `other_feedback` only had deterministic rules for explicit no-more-feedback answers. The `llm_ms` values were 5981.3 and 5951.4 ms; on the final turn the terminal response excluded LLM reply generation, so almost all of its recorded LLM time was the extraction fallback. The captured artifact does not include the server exception log, so a timeout is not established. The first phrase yielded no saved feedback; the second intent phrase was treated as another invalid answer after a retry and the call ended with `other_feedback=null` and `unclear_fields=["other_feedback"]`. The intent phrase now bypasses LLM extraction and asks what the feedback is without consuming a retry or saving the intent text.

For any LLM fallback, DEBUG logs now include expected field, fallback reason, selected parser, original and normalized transcript, and elapsed time. This transcript-bearing line is DEBUG-only because free-text feedback may contain personal details. The INFO answer-extraction trace includes selected method/parser, reason, elapsed time, result, accepted fields, and retry count.

Regression coverage now includes yes/yeah/yep/correct/“that's right”, no/nope, question-aware resolution and technician-arrival answers, empty/ambiguous identity transcripts, no LLM call for clear “Yeah” or invalid decimals, decimal rating/NPS rejection without state advancement, NPS zero, feedback-intent clarification without saving or consuming retries, and null extraction for unclear free-text fallback. The call's “here” transcript is correctly not treated as affirmative; without audio it is not classified as an STT error. No audio, STT model, VAD, barge-in, timer, TTS, WebSocket protocol, or finalization behavior was changed.

Latest focused extraction/state tests: **86 passed**. Final full suite: `.venv\Scripts\python.exe -m pytest -q` — **204 passed in 3.01 s** (loopback-enabled); final `compileall -q app scripts tests websocket_client.py websocket_audio_test.py` passed.
