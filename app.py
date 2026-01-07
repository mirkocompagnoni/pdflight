import os
import tempfile
import subprocess
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Query
from fastapi.responses import HTMLResponse, FileResponse

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

INDEX_HTML = """
<!doctype html>
<html>
<head><meta charset="utf-8"><title>pdflight</title></head>
<body style="font-family: sans-serif; max-width: 720px; margin: 40px auto;">
  <h1>pdflight</h1>
  <p>Carica un PDF e scarica la versione alleggerita.</p>

  <form action="/api/lighten" method="post" enctype="multipart/form-data">
    <label>Preset:
      <select name="preset">
        <option value="screen">screen (massima compressione)</option>
        <option value="ebook" selected>ebook (consigliato)</option>
        <option value="printer">printer (alta qualità)</option>
        <option value="prepress">prepress (molto alta)</option>
      </select>
    </label>
    <br><br>
    <label>OCR:
      <select name="ocr">
        <option value="0" selected>no</option>
        <option value="1">sì</option>
      </select>
    </label>
    <br><br>
    <input type="file" name="file" accept="application/pdf" required />
    <br><br>
    <button type="submit">Alleggerisci</button>
  </form>

  <hr>
  <p>API: POST <code>/api/lighten?preset=ebook&ocr=0</code> con multipart <code>file</code>.</p>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def index():
    return INDEX_HTML

def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)

def _lighten_with_ghostscript(input_pdf: Path, output_pdf: Path, preset: str) -> None:
    gs_setting = GS_PRESETS[preset]
    cmd = [
        "gs",
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.4",
        f"-dPDFSETTINGS={gs_setting}",
        "-dNOPAUSE", "-dQUIET", "-dBATCH",
        f"-sOutputFile={str(output_pdf)}",
        str(input_pdf),
    ]
    _run(cmd)

def _ocr_optimize(input_pdf: Path, output_pdf: Path) -> None:
    # ocrmypdf --optimize 3 è un buon compromesso
    cmd = ["ocrmypdf", "--optimize", "3", "--deskew", str(input_pdf), str(output_pdf)]
    _run(cmd)

@app.post("/api/lighten")
async def lighten(
    file: UploadFile = File(...),
    preset: str = Query(DEFAULT_PRESET, pattern="^(screen|ebook|printer|prepress)$"),
    ocr: int = Query(1 if ENABLE_OCR_DEFAULT else 0, ge=0, le=1),
):
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

        if ocr == 1:
            # OCR può anche aumentare un filo il peso se il PDF è già super compresso,
            # ma in genere migliora ricerca e spesso riduce ancora.
            _ocr_optimize(mid_pdf, out_pdf)
        else:
            out_pdf = mid_pdf

        download_name = f"pdflight_{preset}{'_ocr' if ocr==1 else ''}.pdf"
        return FileResponse(
            path=str(out_pdf),
            media_type="application/pdf",
            filename=download_name,
        )
