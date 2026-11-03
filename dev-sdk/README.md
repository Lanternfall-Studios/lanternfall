# Lanternfall — Developing SDK (v0)

The asset-creation half of Lanternfall's two SDKs (the other half is the
game-playing SDK in lanternfall-core). An agent builds game assets here by
iterating against concept art until the render matches the reference.

## The loop

1. **Concept sheet first.** Every asset starts as a multi-view contact sheet
   (front / side / back / top) in the unified low-poly style, stored in
   `assets/<asset>/concept/`.
2. **Build as code.** The asset is a Blender Python (bpy) script —
   `assets/<asset>/build_<asset>.py` — primitives + the master palette,
   proportions in a PARAMS block at the top. No manual GUI modeling:
   the script IS the asset source, so any agent can re-run and improve it.
3. **Render the same angles.** The build script renders orthographic views
   from fixed cameras matching the contact sheet into `renders/`.
4. **Compare and iterate.** The agent views renders vs. the sheet, adjusts
   params/geometry, re-runs. Only what the render proves counts.
5. **Export.** `mira.glb` (glTF) is the Godot-ready output.

## Running it

```bash
~/workspace/tools/blender/blender -b --python assets/mira/build_mira.py
```

Headless notes for this VM: EEVEE is unavailable (no libEGL), so renders use
Cycles CPU (~10s per 560px view). Blender 4.5.3 LTS lives in
`~/workspace/tools/blender/`. Gotcha: call `read_factory_settings` BEFORE
creating materials — it invalidates earlier material references.

## Assets

- `assets/mira/` — Elder Mira, lantern keeper. Status: v1 verified
  2026-11-03 (4 iterations). Recognizable match in all four views;
  known gaps: blank face (no smile/features beyond glasses), shawl is
  geometric panels rather than soft wrapped cloth (next refinement is
  vertex-level shaping), hair is a smooth dome (no swept strands),
  slightly slimmer proportions than the sheet.
