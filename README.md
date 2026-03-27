# Tori

Web-based 3D diff viewer prototype for comparing CAD revisions, with STEP conversion handled on the backend.

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
