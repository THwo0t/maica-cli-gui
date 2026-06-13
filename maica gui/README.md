# MAICA GUI v0.10.4

Independent PySide6 GUI frontend for MAICA.

The GUI does not start or drive the CLI. It imports `maica cli/engine.py`
directly, while `maica_cli.py` remains available as a debugger console.

## Run

From the repository root:

```powershell
.\run_gui.ps1
```

If `python` is not your Python 3.13 environment:

```powershell
py -3.13 "maica gui\gui_app.py"
```

For the CLI debugger:

```powershell
.\run_cli.ps1
```

For isolated GUI testing without touching the real memory/profile database:

```powershell
.\run_gui_safe.ps1
```

This starts the same GUI with `--safe-test-db`, using
`maica gui/.safe_test/maica_cli_test.db`.

## Assets

The GUI reads:

```text
maica gui assets/runtime/manifest.json
```

The full copied MAS asset snapshot is stored at:

```text
maica gui assets/mas_raw
```

The runtime subset is intentionally small. It contains a spaceroom background,
several Monika PNG layers, common expression layers, and GUI textbox/namebox
assets.

## Current Scope

- Chat with Monika through `MaicaEngine.chat()`.
- `/spire` button through `MaicaEngine.spire()`.
- Default dialogue output is English when `language` is set to `en`.
- Background scene from MAS spaceroom assets.
- Layered PNG avatar preview.
- Emotion metadata changes the visible expression preset.
- MTrigger notices are shown in the chat log.
- Windows SAPI TTS and Aliyun Bailian CosyVoice TTS.
- Persistent background engine worker, so GUI no longer rebuilds the engine every turn.
- Data Manager dialog for profile, nicknames, affection, memories, facts, and safe debug snapshots.
- Settings dialog for common non-secret runtime options.
- Diagnostics export button for safe troubleshooting reports.
- Toggleable Debug panel with compact MFocus/Response Planner summaries.
- Runtime context strip showing date/time, affection, relationship stage, and today's special events.
- Auto/day/night/rain background selection from the runtime asset manifest.
- STT MVP through Windows Speech Recognition with a `Listen` button.
- Optional out-of-process embedding service for GUI vector/RAG retrieval.
- Lite/example-only response planner modes with dual response parsing.
- Rule-only MTrigger for predictable local state updates.
- Manual user-data export/import for portable backups.
- Optional idle `/spire` proactive greeting.
- Automatic lightweight memory summaries.
- UTF-8 source/config validation in smoke tests.

## TTS

The GUI supports provider-style TTS adapters.

### Windows SAPI

```json
{
  "tts_enabled": false,
  "tts_provider": "windows_sapi",
  "tts_voice": "",
  "tts_auto_play": true,
  "tts_rate": 0,
  "tts_volume": 90
}
```

Windows SAPI only works on Windows. On Linux and macOS, use a network or
external TTS provider such as Bailian CosyVoice.

### Aliyun Bailian CosyVoice

`tts_bailian_api_key` belongs only in your ignored local
`maica cli/config.json`. Do not put it in `config.example.json`.

```json
{
  "tts_enabled": true,
  "tts_provider": "bailian_cosyvoice",
  "tts_bailian_api_key": "",
  "tts_bailian_endpoint": "wss://dashscope.aliyuncs.com/api-ws/v1/inference",
  "tts_bailian_model": "cosyvoice-v3.5-plus",
  "tts_bailian_voice": "",
  "tts_bailian_format": "mp3",
  "tts_playback_backend": "auto",
  "tts_playback_command": "",
  "tts_bailian_sample_rate": 22050,
  "tts_bailian_volume": 50,
  "tts_bailian_rate": 1.0,
  "tts_bailian_pitch": 1.0,
  "tts_bailian_timeout": 30,
  "tts_bailian_instruction": "Speak naturally and warmly, like a close daily conversation with a little playful softness."
}
```

The current adapter requests MP3 or WAV audio and plays it through a child
process. Playback backend `auto` chooses a platform-appropriate command:

- Windows: `powershell`, `pwsh`, `ffplay`, or `mpv`.
- Linux: `ffplay` or `mpv`; WAV can also fall back to `paplay` or `aplay`.
- macOS: `afplay`, `ffplay`, or `mpv`.

On Arch Linux, install one of these if TTS synthesis works but playback fails:

```bash
sudo pacman -S ffmpeg
```

or:

```bash
sudo pacman -S mpv
```

This keeps network synthesis and playback outside the Qt main thread.

Before TTS synthesis, the GUI removes bracketed stage directions and metadata
from the spoken text. The visible chat text is unchanged; only the voice line is
cleaned.

## Vector Retrieval In GUI

The CLI debugger can still use embedding/FAISS retrieval normally. The GUI
disables thread-local embedding retrieval by default:

```json
{
  "gui_disable_thread_embeddings": true
}
```

This avoids a PySide6 worker-thread crash observed when sentence-transformers
loads or encodes embeddings inside the GUI worker.

v0.9.1 adds an optional localhost embedding service:

```json
{
  "embedding_enabled": true,
  "memory_embedding_enabled": true,
  "embedding_service_enabled": true,
  "embedding_service_autostart": true,
  "embedding_service_host": "127.0.0.1",
  "embedding_service_port": 8766,
  "embedding_service_timeout": 8
}
```

When service mode is enabled, Example Bank and memory vector retrieval call
`maica cli/embedding_service.py` over HTTP. The service owns FAISS and
sentence-transformers work; the GUI process only sends localhost requests. If
the service is unavailable, Example Bank falls back to lexical retrieval and
chat stays usable. The first real vector request may be slow while the local
embedding model loads; increase `embedding_service_timeout` if you prefer to
wait instead of falling back on that first turn.

## Data Manager

Use the `Data` button in the GUI to open the data manager.

Current scope:

- View and edit profile fields: `player_name`, `birthday`, `location`, `nicknames`, `affection`.
- Add and delete memories.
- Add and delete facts.
- Summarize recent chat into long-term memory notes.
- Export and import user data packages.
- View a compact status/debug summary.
- Export a local debug JSON snapshot with secret-like config values hidden.

Database writes are performed inside the GUI engine worker thread, not directly
from the Qt main thread.

## Settings

Use the `Settings` button to edit common non-secret options:

- API base and model name.
- Output language.
- Temperature, top-p, and max tokens.
- Frequency and presence penalties.
- Streaming toggle for compatible API providers.
- MFocus/MTrigger mode. MTrigger is rule-only in this branch.
- Response output mode and metadata extraction.
- Lite/example-only response planner mode.
- Example Bank retrieval limits, score threshold, and prompt weight.
- TTS provider and Bailian voice/model/format/instruction.
- Example Bank vectors, memory vectors, and external embedding service.
- Background mode: auto, day, night, or rain.
- Idle proactive `/spire`, startup greeting, automatic summaries, and hidden token stats.
- GUI thread embedding safety toggle.

Settings are applied to the persistent backend immediately after saving. New
chat requests use the updated options without restarting the GUI. Secrets such
as API keys are intentionally not displayed in the GUI settings dialog. Keep
editing those only in the ignored local `maica cli/config.json`.

## Runtime Context

The GUI refreshes a small context strip after the backend engine becomes ready
and after data-manager operations. It currently shows:

- Local date and time.
- Affection value.
- Relationship stage.
- Detected special events for today.

The background switches between the default spaceroom and night spaceroom based
on local time when background mode is `auto`. Settings can force `day`, `night`,
or `rain`. The context strip shows the active mode and detected events for
today. Rich holiday/weather overlays are reserved for later resource expansion.

## STT

The `Listen` button uses the configured STT provider and writes recognized text
into the input box.

Current provider:

```json
{
  "stt_provider": "windows_speech",
  "stt_language": "en",
  "stt_timeout": 8
}
```

This MVP uses Windows `System.Speech.Recognition` through a child PowerShell
process. It depends on a working microphone, Windows speech recognition support,
and the selected recognition language being installed. If recognition is not
available, the GUI reports the error and text chat continues normally.

## Diagnostics And Tests

Run:

```powershell
.\run_smoke_tests.ps1
python "maica gui\diagnostics.py"
```

`diagnostics.py` prints a safe JSON report for local troubleshooting. It checks
Python, Git, package availability, runtime paths, and a masked config summary.
It does not print API keys, database rows, memories, or logs.

The GUI `Diagnostics` button exports the same safe report to a JSON file.

The GUI `Debug` button toggles a compact per-reply summary: source, emotion,
response time, planner category/intent/mode, style category, and example-bank
summary scores. It intentionally avoids dumping full prompts or private memory
text.

The smoke suite also runs a no-API fake-client `MaicaEngine.chat()` call against
a temporary database. This catches core prompt/parse/store regressions without
using real API quota or real memories.

## Packaging

Experimental PyInstaller packaging is available from the repository root:

```powershell
.\build_gui_exe.ps1
```

The spec file is `maica gui/maica_gui.spec`. It includes the GUI source, shared
CLI engine files, and the small runtime asset subset. It excludes private
`config.json`, databases, logs, FAISS indexes, TTS caches, and raw MAS assets.
Generated `build/` and `dist/` directories are ignored by Git.

The build creates `maica-gui.exe` plus `maica-embedding-service.exe`; the GUI
uses the service executable only when external embedding service mode is enabled.
First-time PyInstaller builds can take several minutes because PySide6 analysis
is heavy.

The build script packages a sanitized staging copy rather than your live
`maica cli` directory. It then runs `maica gui/package_audit.py` against
`dist/maica-gui` to reject private config, databases, logs, FAISS indexes, model
blobs, TTS caches, raw MAS assets, and secret-like strings.

## Not Yet Included

- Native Live2D model loading.
- Full MAS outfit/accessory parser.
- Event decoration switching.
- Packaged `.exe`.
