FROM odoo:17

USER root

# =========================
# System libraries (minimal)
# =========================
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# =========================
# Python OCR dependencies
# =========================
RUN pip3 install --no-cache-dir \
    typhoon-ocr \
    azure-ai-documentintelligence \
    azure-ai-formrecognizer


USER odoo
