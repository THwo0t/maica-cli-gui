# MAICA GUI v0.8.3

Independent PySide6 GUI frontend for MAICA.

The GUI does not start or drive the CLI. It imports `maica cli/engine.py`
directly, while `maica_cli.py` remains available as a debugger console.

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

The runtime subset is intentionally small. It contains a spaceroom background,
several Monika PNG layers, common expression layers, and GUI textbox/namebox
assets.

## Current Scope

- Chat with Monika through `MaicaEngine.chat()`.
- `/spire` button through `MaicaEngine.spire()`.
- Background scene from MAS spaceroom assets.
- Layered PNG avatar preview.
- Emotion metadata changes the visible expression preset.
- MTrigger notices are shown in the chat log.
- Windows SAPI TTS and Aliyun Bailian CosyVoice TTS.
- Persistent background engine worker, so GUI no longer rebuilds the engine every turn.

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
  "tts_bailian_sample_rate": 22050,
  "tts_bailian_volume": 50,
  "tts_bailian_rate": 1.0,
  "tts_bailian_pitch": 1.0,
  "tts_bailian_timeout": 30,
  "tts_bailian_instruction": "语气自然温柔，像恋人日常聊天，带一点俏皮。"
}
```

The current adapter requests MP3 audio and plays it through a child PowerShell
`System.Windows.Media.MediaPlayer` process. This keeps network synthesis and
playback outside the Qt main thread.

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

## Not Yet Included

- Native Live2D model loading.
- STT.
- Full MAS outfit/accessory parser.
- Event decoration switching.
- Packaged `.exe`.
