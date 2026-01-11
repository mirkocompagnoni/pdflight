import os
import tempfile
import subprocess
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, Response

from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi import Request



APP_NAME = "pdflight"


# Env
MAX_MB = int(os.getenv("PDFLIGHT_MAX_MB", "30"))
DEFAULT_PRESET = os.getenv("PDFLIGHT_DEFAULT_PRESET", "ebook")  # screen|ebook|printer|prepress
ENABLE_OCR_DEFAULT = os.getenv("PDFLIGHT_OCR_DEFAULT", "0") == "1"

GS_PRESETS = {
    "screen": "/screen",
    "ebook": "/ebook",
    "printer": "/printer",
    "prepress": "/prepress",
}

app = FastAPI(title=APP_NAME)
templates = Jinja2Templates(directory="templates")

STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "default_preset": DEFAULT_PRESET,
            "ocr_default": 1 if ENABLE_OCR_DEFAULT else 0,
        },
    )


def _safe_decode(b: bytes) -> str:
    # prova utf-8, se fallisce sostituisce i caratteri non validi
    return b.decode("utf-8", errors="replace")

def _run(cmd: list[str]) -> None:
    p = subprocess.run(cmd, capture_output=True)  # <-- niente text=True
    if p.returncode != 0:
        stderr = _safe_decode(p.stderr or b"")
        stdout = _safe_decode(p.stdout or b"")
        detail = (stderr or stdout or "Command failed").strip()
        detail = detail[-2000:]
        raise HTTPException(status_code=400, detail=detail)


def _lighten_with_ghostscript(input_pdf: Path, output_pdf: Path, preset: str) -> None:
    gs_setting = GS_PRESETS[preset]
    out = str(output_pdf.resolve())
    cmd = [
        "gs",
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.4",
        f"-dPDFSETTINGS={gs_setting}",
        "-dSAFER",
        "-dNOPAUSE", "-dBATCH",
        "-sOutputFile=" + out,
        str(input_pdf),
    ]
    print("GS CMD:", cmd)
    _run(cmd)
    print("GS OUTPUT EXISTS?", output_pdf.exists(), "SIZE:", output_pdf.stat().st_size if output_pdf.exists() else -1)
    if not output_pdf.exists() or output_pdf.stat().st_size == 0:
        raise HTTPException(
            status_code=400,
            detail="Ghostscript did not produce output PDF (output_pdf missing or empty)."
        )



def _qpdf_clean(input_pdf: Path, output_pdf: Path) -> None:
    """
    Pulizia/ottimizzazione "safe" senza OCR: non rasterizza e non altera layout/pagine.
    Richiede qpdf installato nel sistema/container.
    """
    cmd = [
        "qpdf",
        "--object-streams=generate",
        "--stream-data=compress",
        str(input_pdf),
        str(output_pdf),
    ]
    _run(cmd)

def _ocr_optimize(
    input_pdf: Path,
    output_pdf: Path,
    *,
    do_ocr: bool,
    autorotate: bool,
    deskew: bool,
    clean: bool,
    oversample_level: int,
) -> None:
    # oversample in ocrmypdf is expressed in DPI. We expose a simple 1..4 slider.
    oversample_map = {1: 150, 2: 300, 3: 400, 4: 600}
    oversample_level = int(oversample_level or 2)
    oversample_level = max(1, min(4, oversample_level))
    oversample_dpi = oversample_map[oversample_level]

    cmd = ["ocrmypdf"]

    # OCR layer
    if do_ocr:
        cmd += ["--force-ocr", "-l", "ita+eng"]
    else:
        # NOTE: ocrmypdf does not provide a true "no OCR but apply preprocessing" mode.
        # Using --skip-text avoids re-OCRing pages that already contain text.
        cmd += ["--skip-text"]

    # Page preprocessing
    if autorotate:
        cmd += ["--rotate-pages", "--rotate-pages-threshold", "2"]
    if deskew:
        cmd += ["--deskew"]
    if clean:
        cmd += ["--clean"]

    if do_ocr:
        cmd += ["--oversample", str(oversample_dpi)]

    cmd += ["--optimize", "3", str(input_pdf), str(output_pdf)]
    _run(cmd)

@app.post("/api/lighten")
async def lighten(
    file: UploadFile = File(...),
    preset: str = Form(DEFAULT_PRESET),
    ocr: int = Form(1 if ENABLE_OCR_DEFAULT else 0),
    autorotate: int = Form(0),
    deskew: int = Form(0),
    clean: int = Form(1),
    oversample: int = Form(2),
):
    # normalizza e valida (Form non fa pattern/ge/le come Query)
    preset = (preset or DEFAULT_PRESET).strip().lower()
    if preset not in GS_PRESETS:
        raise HTTPException(status_code=400, detail=f"Invalid preset: {preset}")

    try:
        ocr = int(ocr)
    except Exception:
        ocr = 0
    if ocr not in (0, 1):
        raise HTTPException(status_code=400, detail="Invalid ocr value (use 0 or 1).")

    try:
        autorotate = int(autorotate)
    except Exception:
        autorotate = 0
    try:
        deskew = int(deskew)
    except Exception:
        deskew = 0
    try:
        clean = int(clean)
    except Exception:
        clean = 1
    try:
        oversample = int(oversample)
    except Exception:
        oversample = 2

    autorotate = 1 if autorotate else 0
    deskew = 1 if deskew else 0
    clean = 1 if clean else 0
    oversample = max(1, min(4, oversample))

    # Basic guard
    data = await file.read()
    if len(data) > MAX_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File troppo grande (> {MAX_MB} MB).")

    with tempfile.TemporaryDirectory(prefix="pdflight_") as tmp:
        tmpdir = Path(tmp)
        in_pdf = tmpdir / "input.pdf"
        mid_pdf = tmpdir / "light.pdf"
        out_pdf = tmpdir / "output.pdf"

        in_pdf.write_bytes(data)

        _lighten_with_ghostscript(in_pdf, mid_pdf, preset)
        if not mid_pdf.exists() or mid_pdf.stat().st_size == 0:
           raise HTTPException(400, "Ghostscript did not produce output PDF (mid_pdf missing or empty).")

        # --- Pipeline ---
        # Livello 2 (OCR=1): analisi contenuto -> autorotate/deskew/clean/oversample
        # Livello 1 (OCR=0): niente ocrmypdf (evita artefatti sulle scansioni); solo compressione + pulizia safe

        # Se OCR è OFF, autorotate/deskew non devono avere effetto (sono opzioni del livello OCR)
        if ocr == 0:
            autorotate = 0
            deskew = 0

        if ocr == 1:
            _ocr_optimize(
                mid_pdf, out_pdf,
                do_ocr=True,
                autorotate=bool(autorotate),
                deskew=bool(deskew),
                clean=bool(clean),
                oversample_level=oversample,
            )
        else:
            # OCR OFF: evita ocrmypdf. Applica solo una pulizia "safe" se richiesta.
            if bool(clean):
                _qpdf_clean(mid_pdf, out_pdf)
            else:
                out_pdf = mid_pdf

        original_name = Path(file.filename).stem
        original_ext  = Path(file.filename).suffix

        suffix = "_light"
        if ocr == 1:
           suffix += "_ocr"
        elif bool(clean):
           suffix += "_opt"

        download_name = f"{original_name}{suffix}{original_ext}"

        pdf_bytes = out_pdf.read_bytes()   # <-- IMPORTANTISSIMO: leggi dentro il with

        headers = {
            "Content-Disposition": f'attachment; filename="{download_name}"'
        }
        return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)
