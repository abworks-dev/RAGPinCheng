# Local original-image OCR

Opt-in CPU service for original single-frame JPG/JPEG/PNG only. No cloud client, model download, semantic image recognition, model sidecar OCR, or historical-image backfill. Text is a separate attachment search derivative, never a RAG Parent or answer citation.

## Runtime contract

- `POST /v1/ocr`, multipart `file`, `Authorization: Bearer <IMAGE_OCR_TOKEN>`; missing token fails closed. Never log or commit a real token.
- Returns `text`, bounded text/confidence/box `blocks`, `engine`, and content-derived `model_version`. Empty text is a successful `no_text` job upstream.
- Default input limit 32 MiB, 80 million pixels, edge 32768; one request at a time; upload timeout 10 s and isolated worker timeout 25 s. Each worker exits after one image; service-coroutine cancellation or timeout terminates its process. Canceling an application job fences result activation immediately, but an already-running HTTP/native request can continue until its bounded deadline.
- Longest inference image edge is 4096, boxes map back to original coordinates. Small print, rotated text, low contrast, and dense drawings can be inaccurate: OCR is discovery assistance, not verified engineering facts.
- `GET /health` is unauthenticated readiness, not model accuracy verification. API `available` describes configuration only, not this health endpoint.

## Dependencies and licensing

`requirements.in` is the direct input; `requirements.txt` is the universal Python 3.11 resolved lock. Runtime dependencies are isolated from backend Python requirements. RapidOCR ONNX Runtime 1.4.4 wheel includes the models; model file SHA-256 values form the response fingerprint. No first-request download or external fallback is implemented.

Upstream references: [RapidOCR package and license metadata](https://pypi.org/project/rapidocr-onnxruntime/1.4.4/) (Apache-2.0), [RapidOCR](https://github.com/RapidAI/RapidOCR), [ONNX Runtime](https://github.com/microsoft/onnxruntime) (MIT), [PaddleOCR model upstream](https://github.com/PaddlePaddle/PaddleOCR) (Apache-2.0). Preserve upstream package notices and bundled model notices when distributing a built image; the resolved dependencies retain their own licenses. Do not interpret the application license as replacing dependency/model licenses.

## Validation and deployment boundary

Run the nine HTTP-boundary tests in `tests/test_image_ocr_service.py` with CI dependencies, and the three actual model tests in `services/image_ocr/test_engine.py` with this service lock. The checked-in Chinese PNG is synthetic text, not business data. Real-model tests cover English/digits, Chinese UTF-8, and blank images; they are not a representative OCR quality benchmark.

The opt-in overlay `docker/docker-compose.image-ocr.yml` uses an internal network, no published port/business volume/GPU, non-root user, read-only filesystem, bounded temporary filesystem/resources, and token authentication. `IMAGE_OCR_ENABLED` defaults false even with the overlay. Container configuration has been validated without resolving environment files; local Docker daemon was unavailable, so no local image-build/container-run claim is made.

Enabling this service in production, changing credentials/network trust, or backfilling existing real images requires its own explicit approved operational boundary. This implementation does not run those actions. Rollback: disable OCR admission; metadata search/preview remains usable. Existing derived data is retained and source-version/hash/current-head checks remain mandatory.
