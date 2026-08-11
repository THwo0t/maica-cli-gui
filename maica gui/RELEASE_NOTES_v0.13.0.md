# MAICA GUI v0.13.0

This release completes the first stable embedded Live2D integration while
keeping the existing MAICA engine, SQLite data, FAISS indexes, PNG avatar, and
VTube Studio support.

## Upgrade

- Back up your ignored `maica cli/config.json` and `maica cli/maica_cli.db` as
  usual. The release does not rewrite, clear, or migrate user memories.
- Install the dependencies from `maica gui/requirements.txt`. PySide6 must
  include QtMultimedia, QtWebChannel, and QtWebEngine.
- Existing configs remain valid. New audio and Live2D options receive defaults
  from `config_defaults.py`.
- Open Settings and click Save to apply runtime changes immediately.

## Import A Live2D Model

1. Obtain a licensed Cubism 4 model containing a `.model3.json` file.
2. Obtain `live2dcubismcore.min.js` under Live2D's licensing terms.
3. In Settings -> Avatar, choose `embedded_live2d` or `auto`.
4. Select a model directory or use Import ZIP, then select Cubism Core.
5. Click Save or Apply / Test avatar backend.

ZIP imports reject path traversal, symbolic links, encrypted/special entries,
more than 4096 files, and more than 512 MB of expanded data. Referenced model
resources are validated before the renderer starts.

## Stability And Fallback

- Every dialogue turn has an ID, generation, and ordered event sequence.
  Duplicate, post-cancel, and stale results cannot update the GUI.
- Speech uses real Qt playback lifecycle events. Changing the audio output
  cancels the old speech session before rebuilding the audio backend.
- Explicit embedded Live2D reloads Chromium once after a renderer crash.
- `auto` falls back in this order: embedded Live2D, VTube Studio, PNG.
- Missing WebEngine, renderer assets, Cubism Core, or model resources produce a
  concise diagnostic instead of stopping chat.

## Known Limitations

- Only Cubism 4 `.model3.json` models are supported. Cubism 2 is not supported.
- No Monika Live2D model or Cubism Core is bundled.
- Expression and motion names vary by model and may need an advanced mapping
  override.
- Visual Live2D acceptance requires a licensed local model and working WebGL.
- Audio amplitude lip sync is real-time RMS based, not phoneme/viseme analysis.

## Privacy And Packaging

The embedded renderer receives only avatar state, mapped actions, and audio
amplitude. It cannot access MAICA's API keys, database, memories, or file tools.
External top-level navigation and remote content access are disabled.

Git and Release packages exclude private config, databases, logs, FAISS data,
TTS caches, Cubism Core, avatar models, and raw MAS assets. The package audit
also verifies that the prebuilt renderer, expression map, and third-party
notices are present.
