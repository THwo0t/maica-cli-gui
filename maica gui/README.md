# MAICA GUI v0.8.2

Independent PySide6 GUI frontend for MAICA.

The GUI does not start or drive the CLI. It imports `maica cli/engine.py` directly, while `maica_cli.py` remains available as a debugger console.

## Run

From the repository root:

```powershell
python "maica gui\gui_app.py"
```

If `python` is not your Python 3.13 environment:

```powershell
py -3.13 "maica gui\gui_app.py"
```

## Assets

The GUI reads:

```text
maica gui assets/runtime/manifest.json
```

The full copied MAS asset snapshot is stored at:

```text
maica gui assets/mas_raw
```

The runtime subset is intentionally small. It contains a spaceroom background, several Monika PNG layers, common expression layers, and GUI textbox/namebox assets.

## Current Scope

- Chat with Monika through `MaicaEngine.chat()`.
- `/spire` button through `MaicaEngine.spire()`.
- Background scene from MAS spaceroom assets.
- Layered PNG avatar preview.
- Emotion metadata changes the visible expression preset.
- MTrigger notices are shown in the chat log.
- Windows SAPI TTS MVP with a GUI on/off button.
- Persistent background engine worker, so GUI no longer rebuilds the engine every turn.

## Not Yet Included

- Native Live2D model loading.
- STT.
- Full MAS outfit/accessory parser.
- Event decoration switching.
- Packaged `.exe`.

Those should be built after the GUI loop and asset runtime manifest are stable.

## TTS

v0.8.2 adds a dependency-light Windows SAPI TTS adapter.

Config keys:

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

The GUI button can toggle TTS at runtime. This runtime toggle does not rewrite
`config.json`; use the CLI debugger `/config set tts_enabled true` if you want
TTS enabled by default.

## Vector Retrieval In GUI

The CLI debugger can still use embedding/FAISS retrieval normally.

The GUI disables thread-local embedding retrieval by default:

```json
{
  "gui_disable_thread_embeddings": true
}
```

This avoids a PySide6 worker-thread crash observed when sentence-transformers
loads or encodes embeddings inside the GUI worker. The long-term fix is to move
embedding retrieval into a separate subprocess/service, then let the GUI call
that service instead of loading the embedding model inside a Qt worker thread.
