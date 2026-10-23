# Audio asset manifest

All WAV files are repository test assets. Metadata below was inspected locally. The transcripts for the short files have not been independently verified, so their filenames are not treated as transcription ground truth.

| File | Format | Duration | Purpose / transcript status |
|---|---|---:|---|
| `test_speech.wav` | mono, 16 kHz PCM | 6.89 s | Caller speech fixture. The project brief gives an expected utterance (“Hello, I am Rahul…”) but its transcript was not independently run against a recognizer in this environment. |
| `yes.wav`, `yes_1.wav` | mono, 44.1 kHz PCM | 2.23 s, 1.32 s | Candidate identity/yes clips based on filename; speech content not verified. |
| `no.wav` | mono, 44.1 kHz PCM | 1.32 s | Candidate no/wrong-person clip based on filename; content not verified. |
| `rating.wav` | mono, 44.1 kHz PCM | 1.67 s | Candidate rating clip based on filename; score not verified. |
| `5.wav`, `9.wav`, `10.wav` | mono, 44.1 kHz PCM | 1.32 s each | Candidate score clips based on filenames; spoken numbers not verified. |
| `reply.wav`, `reply_1.wav` | mono, 44.1 kHz PCM | 1.53 s, 4.60 s | Candidate response clips; spoken text not verified. |

The repository's current automated unit tests use mocks and do not establish that these files were recognized correctly. Use `websocket_audio_test.py` (or `websocket_client.py`) for explicit audio replay; live microphone input remains the default browser behavior.
