# pdflight
PDF file lightening service

Self-hosted PDF optimizer (Ghostscript + optional OCR).

## Run (Docker)

### Option A: build locally
```bash
docker compose up -d --build


## API

`POST /api/lighten` (multipart form)

Form fields:
- `file` (PDF)
- `preset`: `ebook|screen|printer`
- `ocr`: `0|1`
- `autorotate`: `0|1`
- `deskew`: `0|1`
- `clean`: `0|1`
- `oversample`: `1..4` (used only when `ocr=1`)
