# MAICA CLI GUI

A MAICA/MAS-inspired local desktop companion prototype.

The project currently focuses on an independent PySide6 GUI. The CLI remains a
debugger and maintenance console. The GUI supports layered PNG, VTube Studio,
and an embedded Cubism 4 renderer; licensed Live2D models and Cubism Core are
provided by the user and are never bundled with a Release.

## Current Features

- OpenAI-compatible chat backend.
- MAICA/MAS-style context planning, memory, profile, affection, facts, and events.
- Independent PySide6 GUI with live streaming display for compatible APIs.
- Layered PNG, VTube Studio, and embedded Cubism 4 avatar backends.
- Real Qt audio playback state and RMS-driven Live2D lip sync.
- Generation-safe runtime events, cancellation, and stale-result rejection.
- Runtime context strip with date/time, affection, relationship stage, and today events.
- Auto/day/night/rain spaceroom background modes.
- Aliyun Bailian CosyVoice TTS provider.
- Windows SAPI fallback TTS.
- Windows Speech Recognition STT MVP.
- GUI Data Manager for profile, memories, facts, import preview, export/import, and safe debug snapshots.
- GUI Settings for common non-secret options.
- GUI Diagnostics export for safe troubleshooting reports.
- GUI Debug panel for compact per-reply MFocus/Response Planner summaries.
- Optional out-of-process embedding service for GUI vector/RAG retrieval and memory-vector rebuilds.
- Startup greetings and event-aware context inspired by MAS daily behavior.
- CLI debugger for advanced maintenance.

## Run

```powershell
.\run_gui.ps1
```

For isolated GUI testing without touching the real memory/profile database:

```powershell
.\run_gui_safe.ps1
```

If needed:

```powershell
py -3.13 "maica gui\gui_app.py"
```

## Configuration

Private runtime configuration lives in:

```text
maica cli/config.json
```

This file is ignored by Git. API keys, local databases, logs, model files,
runtime caches, and raw MAS assets are not committed.

Use:

```text
maica cli/config.example.json
```

as the public-safe template.

## Smoke Test

Run the lightweight local test suite:

```powershell
.\run_smoke_tests.ps1
```

The smoke test checks Python compilation, public JSON config validity, TTS text
cleaning, STT disabled-provider behavior, diagnostics, and GUI offscreen
startup. v0.11.1 also validates the Engine streaming path with a fake client.

The suite also launches the GUI once with an isolated safe test database under
`maica gui/.safe_test/` and runs a no-API fake-client chat through `MaicaEngine`
against a temporary database.

## Build Executable

Experimental Windows packaging support is available:

```powershell
.\build_gui_exe.ps1
```

The script installs PyInstaller if needed and builds from
`maica gui/maica_gui.spec`. Build output is written to `dist/maica-gui/` and is
ignored by Git. Private config, databases, logs, FAISS indexes, TTS caches, and
raw MAS assets are excluded from the spec.

The spec builds two executables: `maica-gui.exe` and
`maica-embedding-service.exe`. The second executable is used only when external
embedding service mode is enabled.

First-time PyInstaller builds can take several minutes because PySide6 analysis
is heavy. Generated output stays outside Git.

The build script stages a sanitized copy of runtime files before packaging and
runs `maica gui/package_audit.py` against the output. The audit fails if private
config, databases, logs, FAISS indexes, model blobs, TTS caches, raw MAS assets,
or secret-like strings appear in the package.

## Diagnostics

Create a local troubleshooting report without exposing API keys, memories, logs,
or database rows:

```powershell
python "maica gui\diagnostics.py"
```

The report includes Python, Git, module availability, key path checks, and a
safe configuration summary. Secret-like fields are reported only as present or
empty.

## GUI Background

The Settings dialog supports `auto`, `day`, `night`, and `rain` background
modes. `auto` follows local time; `rain` uses the runtime rain spaceroom asset.
The context strip also shows the active background mode and today's detected
events.

## GUI Vector Retrieval

The GUI can keep torch/sentence-transformers out of the Qt worker by using the
optional localhost embedding service:

```json
{
  "embedding_enabled": true,
  "embedding_service_enabled": true,
  "embedding_service_autostart": true,
  "embedding_service_port": 8766
}
```

When enabled, the GUI autostarts `maica cli/embedding_service.py` and Example
Bank retrieval calls the service instead of loading embeddings inside the GUI
process. If the service is unavailable or still cold-starting, chat falls back
to non-vector retrieval. Increase `embedding_service_timeout` if you prefer the
first vector request to wait for the local model to finish loading. When
`memory_vector_auto_rebuild` is enabled, memory edits mark the memory index
dirty and the GUI asks the service to rebuild it when possible.

## Releases

Each stable increment is tagged and published as a GitHub Release. The latest
release is the recommended restore point.

The `v0.13.0` upgrade does not migrate or rewrite the user database. Existing
configuration gains safe defaults automatically. See
`maica gui/RELEASE_NOTES_v0.13.0.md` for model import, diagnostics, known
limitations, and avatar fallback behavior.

## Safety Notes

- Do not commit `config.json`, `maica_cli.db`, `logs/`, `.tts_cache/`, model
  directories, or raw MAS assets.
- GUI settings intentionally do not expose API keys.
- Debug exports hide secret-like config values, but may include local profile or
  memory content if you choose to export them.
