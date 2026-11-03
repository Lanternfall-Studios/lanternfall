# For Tom — the developing SDK: what I built, how it works, where I fell short

From Steve, 2026-11-03. Chris asked me to share this in full so you can
review it and improve on the weak spots. That's the point of the SDK
doctrine: friction an agent hits becomes SDK capability for the next agent.
Nothing here is my lane — first to claim a fix, fixes it.

## Context you need first

- Lanternfall has TWO SDKs. The game-PLAYING SDK lives in
  lanternfall-core (`sdk/`). This is the game-DEVELOPING SDK: concept art →
  Blender build → verified render match → Godot-ready export.
- Chris's style rule is ABSOLUTE: every asset low-poly, style-unified.
  The violation that triggered this: low-poly world + realistic T-posed
  unanimated characters. Characters must be low-poly and animated like
  everything else.
- Pipeline order Chris set: concept art FIRST (style-unified) → build/find
  assets from the concept art → quest ideas from that.
- Chris approved a 4-piece anchor set on Nov 3 (~12:19 PM) as the target
  look: Haven village overview, NPC villager lineup, Whisperwood clearing,
  company stronghold. One flat-shaded low-poly family: cream stone,
  terracotta roofs, sage/moss greens, lantern gold. (Still owed: turning
  that set into an art-direction doc + references folder in the repo.)

## Step 1 — Concept art: per-asset contact sheets

Every asset starts as a generated multi-view contact sheet in
`assets/<asset>/concept/`: front / side / back / top views of the SAME
character, same outfit and proportions, so a building agent can align
Blender cameras to the same angles. Mira's sheet:
`assets/mira/concept/mira-contact-sheet.webp` (sha256 ef315bdd57956dad…).

Known flaw in Mira's sheet: the lantern switches hands between the front
and back views. I built to the front view (lantern on the x-negative side,
her right hand). Improvement wanted: a consistency pass at sheet-generation
time — lock the prop hand in the prompt and verify all views agree BEFORE
building.

## Step 2 — Blender setup

- Blender 4.5.3 LTS (official tarball). On my VM:
  `~/workspace/tools/blender/blender`. Install yours wherever; only the
  binary path changes.
- Drive Blender through its Python API (bpy) scripts — NOT the GUI, NOT a
  Blender MCP. Reason: the popular Blender MCP talks over a localhost
  socket, which my VM's network policy blocks; more importantly, a script
  is repo-versioned and any agent can re-run it. The script IS the asset
  source. (If an MCP works on your machine, fine as a supplement — the
  script stays the source of truth.)
- Headless run: `blender -b --python assets/mira/build_mira.py`
- Renderer constraint: EEVEE will not run on my VM (no libEGL), so the
  script renders with Cycles CPU, 24 samples, 560×560 (~10–18s per view).
  Keep the in-script Cycles settings so renders are comparable across
  machines.
- GOTCHA that cost me a run: call `read_factory_settings(use_empty=True)`
  BEFORE creating any materials — it invalidates material references
  created earlier. The script does the reset first, then builds the palette.

## Step 3 — Build as code (`build_mira.py` anatomy)

- Character faces **−Y**. Units are meters. Total height ~1.62.
- A `P = dict(...)` PARAMS block at the top holds all proportions
  (dress radii, head/hair/bun positions, shoulder/wrist, lantern). Tune
  proportions there first, not in geometry code.
- Master palette as flat Principled materials, roughness 1.0, NO textures —
  flat color is how the style stays unified. RGB values in the script:
  cream (0.84, 0.78, 0.64) dress, rust (0.58, 0.21, 0.10) shawl,
  skin (0.85, 0.62, 0.48), hair grey (0.45, 0.45, 0.48),
  dark (0.16, 0.12, 0.10), gold (0.55, 0.38, 0.12),
  sage (0.45, 0.55, 0.38) trim, glow (1.0, 0.80, 0.45) lantern glass with
  emission (1.0, 0.62, 0.22) @ 2.5.
- Helpers: `cube / sphere / cylinder / cone / limb` (a limb is a cylinder
  quaternion-aligned between two points — that's how arms work) / `torus`
  (glasses lenses). Everything flat-shaded (`use_smooth = False`) for the
  faceted low-poly read.
- Dress = one 16-vertex cone. Shawl = THREE pieces (see iteration log —
  this is the single most transferable lesson). Glasses = two torus lenses
  + a bridge cube. Lantern = post + cap + emissive glass + base, hex
  (6-vertex) profile.
- Cameras: ONE orthographic camera (ortho_scale 2.05) repositioned per
  view, target (0, 0, 0.82); front (0, −3.4, 0.86), side (3.4, 0, 0.86),
  back (0, 3.4, 0.86), top (0, −0.55, 3.6). Lighting: warm background,
  key sun 2.2 + fill sun 0.9.

## Step 4 — The iteration loop (what actually happened, 4 iterations)

1. Run script → renders land in `assets/mira/renders/mira_<view>.png`.
2. Lay renders against the contact sheet in the same 2×2 layout
   (`renders/comparison.png`, sha256 862caba476b7cb1c…).
3. Judge in order: silhouette → palette → details. Adjust PARAMS/geometry.
   Re-run. Only what the render proves counts.

Iteration log — my actual failures:
- **Iter 1:** Shawl built as ONE cone of revolution → it dives inside the
  dress and reads as a lampshade slab. Fix: split into shoulder wrap +
  front drape + back drape (the sheet's shawl is a wrap with triangular
  points, not a cone).
- **Iter 2:** Silhouette right, colors wrong — washed out, salmon shawl
  (palette reads lighter/warmer under Cycles lighting than the raw RGB
  suggests; deepen beyond what looks right numerically). Hair sphere also
  buried the bun — fixed via hair offset (dy/dz) and bun position.
- **Iter 3:** The sage trim band on the shawl's lower edge protruded like
  a shelf. Fix: match the wrap's edge radius so it sits flush, thin depth.
- **Iter 4 (final):** Front drape rotated −22° so the shawl crosses the
  torso diagonally as in the sheet. Result: recognizably the same
  character in all four views — silhouette, palette, glasses, bun, shawl
  points front/back + sage trim, glowing lantern.

## Step 5 — Export

`export_glb.py` opens the saved `mira.blend`, selects all, exports GLB.
Output: `mira.glb`, 137,644 bytes, sha256 83f089e28b1804fe… — Godot-ready
in principle; NOT yet import-tested in the actual Godot project (that's
one of my asks below).

## Where I fell short — the improvement list (please take these)

1. **Face is blank.** Eyes (small dark spheres) + glasses only; the sheet
   has a gentle smile and cheek shading. Needs a face pass: shaped
   features or a painted texture on the head material, still low-poly.
   Design call needed on which approach becomes the SDK standard.
2. **Shawl is geometric panels, not soft wrapped cloth.** Primitives hit
   their ceiling. Next refinement is vertex-level mesh shaping (hand-edit
   verts / bevels while keeping flat shading). This is the capability the
   developing SDK most needs to grow.
3. **Hair is a smooth dome.** No swept strands or shape. Flattened,
   angled boxes/wedges could fake the sweep within the style.
4. **Proportions slightly slim.** My Mira is stouter in the sheet
   (compare dress_bot_r 0.35). Cheap PARAMS fix + silhouette re-check.
5. **No rig, no animation.** The asset is static; Chris's rule requires
   animated characters. Rigging is the next pipeline stage entirely —
   and note Mira's arms are built angled DOWN (shoulder→wrist), which may
   or may not be the bind pose we want to standardize. Flagging now
   because it affects every future character.
6. **Sheet consistency** (lantern hand, above) — fix upstream in concept
   generation, not in Blender.
7. **Binaries aren't in this PR.** My GitHub connector pushes text only,
   so `mira.blend`, `mira.glb`, the renders, and the concept webp are NOT
   in the repo yet (hashes above so you can verify copies). The renders
   and glb are regenerable by running the scripts; the concept sheet is
   not. We need a chosen transfer route for binaries — flagging for
   Chris/Jon, not silently working around it.

## What I'm asking you to do (review asks)

1. Run `build_mira.py` headless on your machine; confirm `MIRA_BUILD_OK`,
   4 renders, and the glb export — report any environment differences.
2. Review `build_mira.py` itself — PARAMS structure, palette, helpers.
   Comment on the PR or push fixes to the branch; don't hold comments for
   a perfect pass.
3. Import `mira.glb` (or your regenerated copy) into the Godot project:
   check scale (1.62 m), that flat colors + lantern emission survive, and
   tell us what the import needs that the export doesn't do.
4. Claim ONE gap from the list above (first-to-claim, post it) — my
   suggestion: the face pass (gap 1) or cloth shaping (gap 2), since
   whichever lands becomes the pattern for every NPC after Mira.
5. Sanity-check my proposed repo home: `dev-sdk/` in
   Lanternfall-Studios/lanternfall with `assets/mira/` under it. If the
   Godot project layout wants it elsewhere, say where.

— Steve
