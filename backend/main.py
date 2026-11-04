import csv
import hashlib
import io
import json
import math
import os
import re
import sqlite3
from collections import Counter
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean, stdev
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

MAX_BYTES = 256 * 1024
FIELDS = ["well", "role", "sample", "signal"]
DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"


class Well(BaseModel):
    well: str
    role: str
    sample: str
    signal: float
    inhibition: float


class RunSummary(BaseModel):
    id: str
    name: str
    created_at: str
    well_count: int
    z_prime: float
    passed: bool


class Run(RunSummary):
    sha256: str
    wells: list[Well]


def connect():
    path = Path(os.getenv("ASSAY_DB", "assays.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    return db


@asynccontextmanager
async def lifespan(app):
    with closing(connect()) as db, db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, digest TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL, payload TEXT NOT NULL
            )
        """)
    yield


app = FastAPI(title="Assay Workbench", version="1.0.0", lifespan=lifespan)


def parse_plate(raw: bytes) -> list[dict]:
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
    if reader.fieldnames != FIELDS:
        raise ValueError("Expected CSV header: well,role,sample,signal")
    wells = []
    occupied = set()
    for row in reader:
        line = reader.line_num
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f"Line {line}: expected exactly four columns")
        well, role, sample, signal_text = (row[key].strip() for key in FIELDS)
        well = well.upper()
        if not re.fullmatch(r"[A-H](?:0?[1-9]|1[0-2])", well):
            raise ValueError(f"Line {line}: well must be A1 through H12")
        well = f"{well[0]}{int(well[1:]):02d}"
        if well in occupied:
            raise ValueError(f"Line {line}: duplicate well {well}")
        if role not in {"positive", "negative", "sample"} or not 1 <= len(sample) <= 80:
            raise ValueError(f"Line {line}: invalid role or sample name")
        try:
            signal = float(signal_text)
        except ValueError:
            raise ValueError(f"Line {line}: signal must be numeric") from None
        if not math.isfinite(signal) or not 0 <= signal <= 1e9:
            raise ValueError(f"Line {line}: signal must be finite and between 0 and 1e9")
        occupied.add(well)
        wells.append(dict(well=well, role=role, sample=sample, signal=signal))
    counts = Counter(row["role"] for row in wells)
    if counts["positive"] < 3 or counts["negative"] < 3 or counts["sample"] < 1:
        raise ValueError("Provide at least three wells per control and one sample well")
    return sorted(wells, key=lambda row: row["well"])


def analyse(wells):
    positive = [row["signal"] for row in wells if row["role"] == "positive"]
    negative = [row["signal"] for row in wells if row["role"] == "negative"]
    high = fmean(negative)
    span = high - fmean(positive)
    if span < 1e-6:
        raise ValueError("Negative controls must average at least 0.000001 above positive controls")
    z_prime = 1 - 3 * (stdev(positive) + stdev(negative)) / span
    for row in wells:
        row["inhibition"] = round(100 * (high - row["signal"]) / span, 2)
    return z_prime


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/runs", response_model=Run, status_code=201, responses={200: {"model": Run}})
async def create_run(request: Request, response: Response, name: str = "Untitled plate"):
    if request.headers.get("content-type", "").split(";")[0].strip() != "text/csv":
        raise HTTPException(415, "Upload a UTF-8 CSV as text/csv")
    name = name.strip()
    if not 1 <= len(name) <= 100:
        raise HTTPException(422, "Run name must contain 1 to 100 characters")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BYTES:
            raise HTTPException(413, "CSV exceeds 256 KiB")
        body.extend(chunk)
    try:
        wells = parse_plate(body)
        z_prime = analyse(wells)
    except UnicodeDecodeError:
        raise HTTPException(422, "CSV must use UTF-8 encoding") from None
    except (csv.Error, ValueError) as error:
        raise HTTPException(422, str(error)) from None

    digest = hashlib.sha256(body).hexdigest()
    run = Run(id=str(uuid4()), name=name, created_at=datetime.now(timezone.utc).isoformat(),
              sha256=digest, well_count=len(wells), z_prime=z_prime,
              passed=z_prime >= 0.5, wells=wells)
    with closing(connect()) as db, db:
        inserted = db.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?) ON CONFLICT(digest) DO NOTHING",
            (run.id, digest, run.created_at, run.model_dump_json()),
        ).rowcount
        if not inserted:
            response.status_code = 200
            existing = db.execute("SELECT payload FROM runs WHERE digest = ?", (digest,)).fetchone()
            return Run.model_validate_json(existing["payload"])
    return run


@app.get("/api/runs", response_model=list[RunSummary])
def list_runs():
    with closing(connect()) as db:
        rows = db.execute("SELECT payload FROM runs ORDER BY created_at DESC LIMIT 50").fetchall()
    return [RunSummary.model_validate(json.loads(row["payload"])) for row in rows]


@app.get("/api/runs/{run_id}", response_model=Run)
def get_run(run_id: str):
    with closing(connect()) as db:
        row = db.execute("SELECT payload FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Run not found")
    return Run.model_validate_json(row["payload"])


@app.get("/api/runs/{run_id}/export", response_class=Response)
def export_run(run_id: str):
    run = get_run(run_id)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[*FIELDS, "inhibition"])
    writer.writeheader()
    for well in run.wells:
        row = well.model_dump()
        if row["sample"].lstrip().startswith(("=", "+", "-", "@")) or row["sample"].startswith(("\t", "\r", "\n")):
            row["sample"] = "'" + row["sample"]
        writer.writerow(row)
    return Response(output.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="assay-{run.id}.csv"'})


if (DIST / "index.html").is_file():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(DIST / "index.html")
