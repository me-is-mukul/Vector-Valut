# Attribution

## Pretrained models

| Model | Source | License | Used for |
|---|---|---|---|
| Qwen 2.5 7B Instruct (Q4_K_M GGUF) | Alibaba Cloud, via the Ollama registry | Apache-2.0 | Answer generation, filing plans |
| bge-small-en-v1.5 | BAAI (Hugging Face) | MIT | Text embeddings |
| clip-ViT-B-32 | OpenAI CLIP weights, packaged by sentence-transformers | MIT (packaging) / OpenAI CLIP license | Image and description embeddings |

## Datasets

None. The app trains nothing; it ships a **hand-written sample curriculum**
([src/osdc/data/curriculum.yaml](src/osdc/data/curriculum.yaml)) that users replace with
their own. Calibration probes in [scripts/calibrate.py](scripts/calibrate.py) are
hand-written snippets.

## Runtimes and key libraries

| Component | License |
|---|---|
| Ollama (local LLM runtime) | MIT |
| PyTorch, sentence-transformers, transformers | BSD-3 / Apache-2.0 |
| NiceGUI (UI), pywebview (window), pystray (tray) | MIT / BSD / LGPL-3.0 |
| FastAPI, uvicorn, pydantic, SQLAlchemy, Alembic, watchdog | MIT / BSD / Apache-2.0 |
| PyMuPDF (PDF extraction) | **AGPL-3.0** (noted: viral license; commercial use would need the Artifex license) |
| pdfplumber, python-docx, python-pptx | MIT |
| Pillow + pillow-heif (HEIC decoding, wraps libheif) | MIT-CMU / BSD-3 + LGPL (libheif) |
| filetype, platformdirs, psutil, tomli-w, PyYAML | MIT / BSD / Apache-2.0 |
| ChromaDB (optional dev vector store; not shipped in the MSI) | Apache-2.0 |
| cx_Freeze (MSI packaging) | PSF-2.0 |

## Pre-existing work

The application code in this repository was written for this project. No external
application code was vendored; all third-party functionality arrives through the
dependencies listed above. AI coding assistants were used during development; all code was
reviewed, tested (132 tests in CI) and is maintained by the authors.
