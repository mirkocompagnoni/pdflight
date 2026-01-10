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


def _ocr_optimize(input_pdf: Path, output_pdf: Path) -> None:
    cmd = [
        "ocrmypdf",
        "--force-ocr",
        "-l", "ita+eng",

        # per scansioni tipo "Tecnocasa" (spesso ruotate / impaginate)
        "--rotate-pages",
        "--rotate-pages-threshold", "2",

        # migliora su testo piccolo e pagine ricche di immagini
        "--deskew",
        "--clean",
        "--oversample", "400",

        "--optimize", "3",
        str(input_pdf),
        str(output_pdf),
    ]
    _run(cmd)



@app.post("/api/lighten")
@app.post("/api/lighten")
async def lighten(
    file: UploadFile = File(...),
    preset: str = Form(DEFAULT_PRESET),
    ocr: int = Form(1 if ENABLE_OCR_DEFAULT else 0),
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


    # Basic guard
    data = await file.read()
    if len(data) > MAX_MB * 1024 * 1024:
        raise ValueError(f"File troppo grande (> {MAX_MB} MB).")

    with tempfile.TemporaryDirectory(prefix="pdflight_") as tmp:
        tmpdir = Path(tmp)
        in_pdf = tmpdir / "input.pdf"
        mid_pdf = tmpdir / "light.pdf"
        out_pdf = tmpdir / "output.pdf"

        in_pdf.write_bytes(data)

        _lighten_with_ghostscript(in_pdf, mid_pdf, preset)
        if not mid_pdf.exists() or mid_pdf.stat().st_size == 0:
           raise HTTPException(400, "Ghostscript did not produce output PDF (mid_pdf missing or empty).")

        if ocr == 1:
            # OCR può anche aumentare un filo il peso se il PDF è già super compresso,
            # ma in genere migliora ricerca e spesso riduce ancora.
            _ocr_optimize(mid_pdf, out_pdf)
        else:
            out_pdf = mid_pdf

        original_name = Path(file.filename).stem
        original_ext  = Path(file.filename).suffix

        suffix = "_light"
        if ocr == 1:
           suffix += "_ocr"

        download_name = f"{original_name}{suffix}{original_ext}"

        pdf_bytes = out_pdf.read_bytes()   # <-- IMPORTANTISSIMO: leggi dentro il with

        headers = {
            "Content-Disposition": f'attachment; filename="{download_name}"'
        }
        return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)
