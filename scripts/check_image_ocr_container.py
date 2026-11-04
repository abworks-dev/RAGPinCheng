"""Exercise the built, isolated OCR container using checked-in synthetic text only."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import uuid


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    fixture = Path(__file__).resolve().parents[1] / 'services/image_ocr/synthetic-chinese.png'
    name = 'ocr-synthetic-' + uuid.uuid4().hex[:12]
    token = 'ci-synthetic-not-a-production-secret'
    command = ['docker', 'run', '--detach', '--name', name, '--network', 'none',
               '--read-only', '--tmpfs', '/tmp:size=128m,mode=1777', '--memory', '1536m',
               '--cpus', '2', '--pids-limit', '64', '--cap-drop', 'ALL',
               '--security-opt', 'no-new-privileges:true',
               '--mount', f'type=bind,source={fixture},target=/synthetic.png,readonly',
               '--env', f'IMAGE_OCR_TOKEN={token}', args.image]
    # This token and mounted image are synthetic CI fixtures. No production config is read.
    probe = r'''
import json, time, urllib.request, urllib.error
from pathlib import Path
for attempt in range(60):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8091/health', timeout=2) as response:
            assert json.load(response)['status'] == 'ok'
        break
    except (OSError, AssertionError):
        time.sleep(1)
else:
    raise RuntimeError('image_ocr_container_health_timeout')
try:
    urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8091/v1/ocr', data=b'bad'), timeout=5)
except urllib.error.HTTPError as error:
    assert error.code == 401, error.code
else:
    raise AssertionError('missing_token_was_not_rejected')
boundary = 'synthetic-image-boundary'
body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="synthetic.png"\r\nContent-Type: image/png\r\n\r\n'.encode()
        + Path('/synthetic.png').read_bytes() + f'\r\n--{boundary}--\r\n'.encode())
request = urllib.request.Request('http://127.0.0.1:8091/v1/ocr', data=body, headers={
    'Content-Type': f'multipart/form-data; boundary={boundary}',
    'Authorization': 'Bearer ci-synthetic-not-a-production-secret'})
with urllib.request.urlopen(request, timeout=40) as response:
    result = json.load(response)
text = ''.join(result['text'].split())
assert '\u6d88\u9632\u901a\u9053' in text and '110' in text, text
assert result['engine'] == 'rapidocr-onnxruntime/1.4.4'
assert result['model_version'].startswith('bundled-sha256:')
print('IMAGE_OCR_CONTAINER status=verified synthetic_chinese=true auth=true network=none read_only=true')
'''
    try:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, timeout=60)
        subprocess.run(['docker', 'exec', name, 'python', '-c', probe], check=True, timeout=120)
    finally:
        subprocess.run(['docker', 'rm', '--force', name], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=30)


if __name__ == '__main__':
    main()
