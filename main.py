import json
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
STORAGE_DIR = BASE_DIR / "storage"
UPLOAD_DIR = STORAGE_DIR / "uploads"
MODEL_DIR = STORAGE_DIR / "models"

for directory in (STORAGE_DIR, UPLOAD_DIR, MODEL_DIR):
    directory.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="Tori CAD Diff Viewer",
    description="Web prototype for visually comparing CAD revisions with backend STEP conversion.",
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/models", StaticFiles(directory=MODEL_DIR), name="models")

SUPPORTED_EXTENSIONS = {".step", ".stp", ".json"}


class ModelImportResponse(BaseModel):
    model_id: str
    scene_name: str
    asset_kind: Literal["glb", "three-json"]
    asset_url: str
    original_filename: str


async def save_upload_file(upload: UploadFile, destination: Path) -> None:
    with destination.open("wb") as handle:
        while chunk := await upload.read(1024 * 1024):
            handle.write(chunk)


def convert_step_to_glb(source_path: Path, output_path: Path) -> None:
    try:
        import cadquery as cq
    except ImportError as exc:
        raise RuntimeError("CadQuery is not installed on the backend.") from exc

    try:
        assembly = cq.Assembly.importStep(str(source_path))
    except ValueError as exc:
        if "does not contain an assembly" not in str(exc):
            raise

        imported = cq.importers.importStep(str(source_path))
        shapes = imported.vals()
        if not shapes:
            raise RuntimeError("STEP file did not produce any shapes.") from exc

        shape = shapes[0] if len(shapes) == 1 else cq.Compound.makeCompound(shapes)
        assembly = cq.Assembly(shape, name=source_path.stem)

    assembly.export(
        str(output_path),
        "GLB",
        tolerance=0.08,
        angularTolerance=0.1,
    )


def validate_three_json(path: Path) -> None:
    try:
        with path.open() as handle:
            json.load(handle)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Uploaded JSON is not valid JSON.") from exc


@app.get("/", include_in_schema=False)
def read_index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def read_favicon() -> FileResponse:
    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


@app.get("/.well-known/appspecific/com.chrome.devtools.json", include_in_schema=False)
def read_chrome_devtools_probe() -> Response:
    return Response(status_code=204)


@app.get("/api/health")
def read_health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/models/import")
def describe_import_model() -> dict[str, str]:
    return {"detail": "Use POST with multipart form-data and a file field named 'file'."}


@app.options("/api/models/import")
def import_model_options() -> Response:
    return Response(status_code=204)


@app.post("/api/models/import", response_model=ModelImportResponse)
async def import_model(file: UploadFile = File(...)) -> ModelImportResponse:
    filename = file.filename or "model"
    extension = Path(filename).suffix.lower()
    created_paths: list[Path] = []

    if extension not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Upload .step, .stp, or .json.",
        )

    model_id = uuid4().hex
    scene_name = Path(filename).stem

    try:
        if extension in {".step", ".stp"}:
            source_path = UPLOAD_DIR / f"{model_id}{extension}"
            output_path = MODEL_DIR / f"{model_id}.glb"
            await save_upload_file(file, source_path)
            created_paths.extend([source_path, output_path])
            convert_step_to_glb(source_path, output_path)

            return ModelImportResponse(
                model_id=model_id,
                scene_name=scene_name,
                asset_kind="glb",
                asset_url=f"/models/{output_path.name}",
                original_filename=filename,
            )

        output_path = MODEL_DIR / f"{model_id}.json"
        await save_upload_file(file, output_path)
        created_paths.append(output_path)
        validate_three_json(output_path)

        return ModelImportResponse(
            model_id=model_id,
            scene_name=scene_name,
            asset_kind="three-json",
            asset_url=f"/models/{output_path.name}",
            original_filename=filename,
        )
    except HTTPException:
        for path in created_paths:
            path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        for path in created_paths:
            path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to import {filename}: {exc}",
        ) from exc
