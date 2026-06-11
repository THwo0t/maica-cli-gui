# MAICA GUI Assets

This folder stores the asset copy and runtime subset for the independent GUI.

## Structure

- `mas_raw/`: copied MAS loose assets and RPA archives. This is the complete local snapshot for future GUI work.
- `runtime/`: small curated subset used by `maica gui/gui_app.py`.
- `copy_summary.json`: copy metadata and total size.

The GUI should normally read only `runtime/manifest.json`. The raw copy is kept for future wardrobe, expression, event, and Live2D preparation work.

## Notes

The current v0.8.1 GUI uses layered PNG rendering, not native Live2D. Live2D can be added later by mapping engine emotion/action metadata to a Live2D model.
