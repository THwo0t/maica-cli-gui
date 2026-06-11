# GUI MVP Resource Recommendations

This note groups the MAS assets by how useful they are for an independent MAICA GUI.

## Tier 1: High-Value For First GUI

- `room_backgrounds`: direct background scene candidates. Start with `MAS/game/mod_assets/location/spaceroom/spaceroom.png`.
- `weather_window_masks`: day, night, sunset, rain, snow, and overcast atmosphere overlays.
- `character_face_expression_layers`: emotion-driven face and eye layers. This is the best bridge from `response_meta.emotion` to visible expression.
- `character_body_base_layers`, `character_hair_layers`, `character_clothes_layers`, `character_accessory_layers`: useful for a layered static Monika renderer.
- `character_definition_json`: useful if we want to reconstruct MAS sprite composition rules instead of manually guessing layer order.
- `renpy_gui_assets`: textbox, namebox, choice menu, button, and overlay references for a first visual style.
- `font_assets`: possible UI font candidates.

## Tier 2: Event And Atmosphere

- `event_birthday_room_decor`: birthday cake and room decorations.
- `event_christmas_room_decor`: Christmas tree, garlands, gifts, lights, and mistletoe overlays.
- `event_halloween_room_decor`: pumpkins, candles, bats, webs, ghost/window overlays, and vignette effects.
- `calendar_assets`: useful for future date/event UI.
- `bgm_audio` and `sfx_audio`: optional BGM and UI/event sound feedback.

## Tier 3: Later Optional Features

- `minigame_assets`: chess, hangman, NOU, piano, and pong. Useful after chat GUI is stable.
- `poem_assets`: poem/event screens.
- `thumbnail_assets`: outfit/accessory preview UI.
- `character_cg`: gallery or special event illustration.
- `console_assets`, `frame_assets`, `custom_button_assets`: useful reference material, but not required for the MVP.

## Live2D Reality Check

MAS assets are Ren'Py layered sprites, not native Live2D files. We have three practical paths:

1. Build a layered 2D sprite renderer first. This is the fastest and most faithful path.
2. Use the MAS layers as reference/cutting material for a future Live2D rig.
3. Use a separate Live2D model and map MAICA `emotion/action` metadata to Live2D parameters.

For v0.8 GUI work, path 1 is the safest MVP: spaceroom background, layered Monika PNGs, emotion expression swaps, chat panel, and optional TTS.

## Suggested Next Technical Step

Create a small `assets_runtime` manifest for the GUI app:

- `background.default`: one spaceroom image.
- `background.weather.*`: window/weather overlays.
- `monika.expression.smile/concerned/shy/playful`: a small manually chosen expression layer set.
- `ui.textbox`: one textbox asset or a redesigned Qt/CSS equivalent.
- `audio.ui.*`: optional click/notify sounds.

This keeps the first GUI small while preserving a path toward the full MAS-style wardrobe and event system.
