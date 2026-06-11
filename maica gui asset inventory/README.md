# MAICA GUI Asset Inventory

This folder is an index of potentially reusable MAS visual/audio/font resources for a future standalone MAICA GUI. It does not copy the original assets; it only records paths, dimensions, categories, and recommended use.

## Files

- `asset_manifest.csv`: full categorized asset manifest.
- `summary.json`: machine-readable counts by category and extension.
- `category_summary.md`: compact category overview.
- `representative_assets.md`: small sample list from each category.

## Practical Notes

- MAS Monika resources are layered Ren'Py sprite assets, not ready-made Live2D models.
- For a GUI MVP, the highest-value assets are `room_backgrounds`, `renpy_gui_assets`, `character_face_expression_layers`, `character_clothes_layers`, `character_hair_layers`, and `weather_window_masks`.
- For Live2D, these assets can serve as reference/cutting material, but they would still need rigging and expression binding.
- Event overlays from birthday, Christmas, and Halloween are useful for calendar-driven GUI atmosphere.
- RPA archives are listed separately. Extract them only if loose assets are insufficient.
