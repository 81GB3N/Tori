# Fusion History Converter

This repo now includes a queue-based Fusion workflow for `.f3d` files:

- `scripts/inspect_f3d.py` inspects an archive and reports document metadata, segment names, and coarse feature hints such as sketch and extrude presence.
- `scripts/tori_fusion_workflow.py export-package` submits a job to Fusion that exports `.step` and writes a history sidecar JSON.
- `scripts/tori_fusion_workflow.py diff-package` compares two history sidecars.
- `scripts/tori_fusion_workflow.py rehydrate-package` submits a job to Fusion that rebuilds a rough `.f3d` from the sidecar or falls back to STEP import.
- `fusion_addin/ToriBridge` is the Fusion add-in that processes those jobs.

## Supported v1 edits

- Existing user-parameter updates by name
- Existing origin-rectangle sketch updates by name
- Existing one-sided new-body extrude updates by name

The writer path is intentionally Fusion-native. The inspector can read `.f3d` directly, but it does not try to regenerate `.f3d` archives by itself.

## One-time setup

Run:

```bash
python3 scripts/install_fusion_bridge.py
```

Then follow the printed `ln -sfn ...` command to place the add-in in Fusion's Add-Ins folder. After that:

1. Open Fusion 360.
2. Go to `Utilities -> Scripts and Add-Ins -> Add-Ins`.
3. Run `ToriBridge`.
4. Enable `Run on Startup`.

After that one-time setup, the shell workflow can launch Fusion and hand jobs to the bridge automatically.

## Patch format

See `examples/fusion/cube_patch.template.json` for a working template. The required fields are:

- `input`: source `.f3d`
- `output`: destination `.f3d`
- `parameters`: parameter-name to expression map, such as `"depth": "15 mm"`
- `sketches`: optional list of sketch edits
- `extrudes`: optional list of extrude edits

## Running the inspector

```bash
python3 scripts/inspect_f3d.py /absolute/path/to/model.f3d
python3 scripts/inspect_f3d.py --json /absolute/path/to/model.f3d
```

## Workflow commands

Export `.f3d` to `.step` and preserve rough history:

```bash
python3 scripts/tori_fusion_workflow.py export-package /absolute/path/to/input.f3d /absolute/path/to/output.step
```

Run the full export -> diff -> rehydrate workflow in one command and store outputs under a hidden ignored project folder:

```bash
python3 scripts/f3d_roundtrip.py /absolute/path/to/input.f3d
```

This writes artifacts under `.tori-fusion-artifacts/<model-name>/` and keeps stable symlinks in `.tori-fusion-artifacts/<model-name>/current/`.
It also writes logs under `.tori-fusion-logs/<model-name>/`.

Diff two preserved history packages:

```bash
python3 scripts/tori_fusion_workflow.py diff-package examples/fusion/sample_left.history.json examples/fusion/sample_right.history.json
```

Roughly rehydrate a `.step + .history.json` package back to `.f3d`:

```bash
python3 scripts/tori_fusion_workflow.py rehydrate-package /absolute/path/to/output.step.history.json /absolute/path/to/output.f3d
```

Capture history from an existing rebuilt `.f3d`:

```bash
python3 scripts/tori_fusion_workflow.py capture-history /absolute/path/to/output.rehydrated.f3d /absolute/path/to/output.rehydrated.history.json
```

## What Each Output Means

- `*.step`
  Exported by Fusion's native STEP exporter.
- `*.step.history.json`
  Sidecar JSON stored next to the STEP. It contains rough captured history from Fusion plus archive inspection hints from the source `.f3d`.
- `*.diff.json`
  Diff between the current source-history sidecar and the previous run's source-history sidecar for the same model.
- `*.rehydrated.f3d`
  A reconstructed Fusion file built from the STEP plus the sidecar history. It is not a byte-identical copy of the original `.f3d`.
- `*.rehydrated.history.json`
  Captured history from the reconstructed `.f3d`.
- `*.rehydrated.history.diff.json`
  Diff between the original source-history sidecar and the rebuilt-history capture. This tells you what the reconstruction preserved or changed.

## What "preserve history" means in v1

- The export step writes a sidecar JSON next to the `.step` file.
- That sidecar records:
  - user parameters
  - sketch and extrude feature names
  - rough expressions for rectangle width/height and extrude distance
  - inspector hints from the original `.f3d`
- The rehydrate step tries to rebuild a simple sketch-plus-extrude design natively in Fusion when enough information exists.
- If the sidecar is not detailed enough, the rehydrate step falls back to importing the STEP and recreating parameters only.
- The rebuilt file that opens as `*.rehydrated.f3d` is the reconstructed result of that process.

This is a prototype. It is not a full fidelity `.f3d -> STEP -> .f3d` round-trip yet.

## Notes

- `.f3d` payloads in these samples use ZIP method `93`, which is Zstandard-compressed.
- The inspector depends on a `zstd` CLI being available on `PATH`.
- The Fusion bridge requires Fusion 360 to be installed at `~/Applications/Autodesk Fusion.app`.
- The Fusion bridge writes queue state under `.tori-fusion-bridge/` in the repo.
