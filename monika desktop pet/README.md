# Monika Desktop Pet

A standalone local Monika desktop pet prototype.

It is intentionally independent from `maica cli` and `maica gui`:

- no LLM calls
- no MAICA engine import
- no API key
- no database access
- local JSON config/state only
- bundled runtime PNG assets under `assets/runtime`

## Run

```bash
python "monika desktop pet/monika_pet.py"
```

or on Windows PowerShell:

```powershell
py -3.13 "monika desktop pet\monika_pet.py"
```

## Features

- Transparent frameless always-on-top desktop pet window.
- Drag with left mouse button.
- Click / double click interactions.
- Right-click context menu.
- System tray menu when available.
- Layered Monika sprite rendering with front hair above head.
- Alpha cropping, soft shadow, breathing animation, bounce/wiggle/nod actions.
- Speech bubble with local scripted lines.
- Startup greeting with simple holiday/event awareness.
- Idle proactive lines.
- Gentle water/stretch reminders.
- Focus timer.
- Tiny local notes.
- Local settings dialog for scale, opacity, idle interval, reminder interval, and focus length.

## Local files

Generated at runtime and intentionally local:

- `pet_config.json`
- `pet_state.json`

These files contain only desktop-pet settings and small local state.

## Notes

This is not Live2D yet. It is a polished layered-PNG desktop pet foundation so
we can later swap the renderer for Live2D without touching the interaction model.
