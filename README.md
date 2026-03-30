# Tori

Web-based 3D diff viewer prototype for comparing CAD revisions, with STEP conversion handled on the backend.

The repo also includes a Fusion 360 history-preserving converter prototype:

- inspect `.f3d` archives with `python3 scripts/inspect_f3d.py /path/to/file.f3d`
- export `.f3d` to `.step` plus a history sidecar with `python3 scripts/tori_fusion_workflow.py export-package /path/to/input.f3d /path/to/output.step`
- diff two history sidecars with `python3 scripts/tori_fusion_workflow.py diff-package left.history.json right.history.json`
- roughly rehydrate a sidecar package back to `.f3d` with `python3 scripts/tori_fusion_workflow.py rehydrate-package /path/to/output.step.history.json /path/to/output.f3d`
- run the whole round-trip with `python3 scripts/f3d_roundtrip.py /path/to/input.f3d`

See `docs/fusion-history-converter.md` for the converter details.

## Fusion Round-Trip

`python3 scripts/f3d_roundtrip.py /path/to/input.f3d` currently does this:

1. Opens the source `.f3d` in Fusion.
2. Exports a `.step` using Fusion's own STEP exporter.
3. Writes a `.step.history.json` sidecar with rough captured design intent.
4. Diffs that sidecar against the previous run for the same model.
5. Rebuilds a new `.f3d` from the `.step` plus the sidecar history.
6. Captures history from that rebuilt `.f3d`.
7. Diffs source history vs rebuilt history.

`*.rehydrated.f3d` is a reconstructed model, not a byte-for-byte copy of the original Fusion file. In the supported case, it is rebuilt from captured Fusion history using named parameters and replayed sketch/extrude definitions, with STEP kept as a geometry fallback.

Current native rebuild support covers:

- sketches made from lines, arcs, circles, and fitted splines
- origin rectangles with named width and height parameters
- multiple supported extrudes, including per-extrude operation, direction, sketch, and profile selection

Current fallback-only features include:

- lofts
- fillets
- move features
- scale features
- projected/reference sketch geometry
- control-point and fixed splines

Outputs are stored under:

- `/Users/jonas/Desktop/code/Tori/.tori-fusion-artifacts/`
- `/Users/jonas/Desktop/code/Tori/.tori-fusion-logs/`

## What it does

- Serves a browser UI from FastAPI
- Accepts two Three.js JSON scene/object exports or STEP files through the backend
- Converts STEP uploads to GLB on the server with CadQuery/OpenCascade
- Renders baseline, candidate, and a visual diff view side by side
- Highlights added, removed, and modified renderable nodes
- Includes demo aircraft revisions so the app opens with meaningful sample data

## Run it

```bash
.venv/bin/uvicorn main:app --reload
```

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000).

## Notes

- The viewer matches objects by hierarchy path and object name, which works well for a prototype when model structure is reasonably stable between revisions.
- JSON uploads should be compatible with `THREE.ObjectLoader`, such as exports from `Object3D.toJSON()`.
- STEP uploads are imported and triangulated on the backend with CadQuery, then served to the browser as GLB.
- The frontend still renders interactively in Three.js so orbiting, overlay diffing, and synchronized camera controls stay fast in the browser.
