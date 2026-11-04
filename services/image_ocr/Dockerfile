FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 libgl1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && python -c "import pathlib,rapidocr_onnxruntime as r; assert len(list((pathlib.Path(r.__file__).parent/'models').glob('*.onnx'))) >= 3"
RUN groupadd --gid 10001 ocr && useradd --uid 10001 --gid ocr --no-create-home ocr
COPY app.py worker.py ./
USER ocr
EXPOSE 8091
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8091", "--workers", "1", "--no-access-log"]
