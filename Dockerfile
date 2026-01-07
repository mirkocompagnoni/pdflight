FROM python:3.12-slim

# System deps: ghostscript + ocrmypdf (che tira dentro tesseract e poppler utils a seconda della distro)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ghostscript \
    ocrmypdf \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py /app/

ENV PYTHONUNBUFFERED=1
ENV PDFLIGHT_MAX_MB=30
ENV PDFLIGHT_DEFAULT_PRESET=ebook
ENV PDFLIGHT_OCR_DEFAULT=0

EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
