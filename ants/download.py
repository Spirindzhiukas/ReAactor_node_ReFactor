"""Safe model-file downloads.

Improvements over the raw ``urllib.request.urlretrieve`` approach:
- request timeout (a stalled mirror can no longer hang ComfyUI startup),
- atomic ``.part`` temp file (a failed download never poisons the models dir),
- HTTP resume (Range) when a partial ``.part`` file exists,
- minimum-size sanity check,
- honor ``REFACTOR_NO_AUTO_DOWNLOAD=1``: raise with the manual URL instead.

Downloads are HTTPS to explicit, versioned URLs only (huggingface.co / github
release assets). No third-party pip index, no wheels, no executables — binary
DLL acquisition stays 100% manual (see ``ants/dlssnr``).
"""

import os
import urllib.request

from .env import NO_AUTO_DOWNLOAD
from .log import logger

DEFAULT_TIMEOUT = 30  # seconds, per connection attempt


def _auto_download_enabled() -> bool:
    # Read at call time so users can toggle the flag without restarting ComfyUI.
    return not (NO_AUTO_DOWNLOAD or os.environ.get("REFACTOR_NO_AUTO_DOWNLOAD", "").strip().lower()
                in ("1", "true", "yes", "on"))


def safe_download(url: str, path: str, name: str = None, min_bytes: int = 1024, timeout: int = DEFAULT_TIMEOUT):
    """Download ``url`` to ``path`` atomically, with resume and sanity checks."""
    name = name or os.path.basename(path) or url.rsplit("/", 1)[-1]
    if os.path.exists(path):
        return path

    if not _auto_download_enabled():
        raise FileNotFoundError(
            f"[ReFactor] Model file '{name}' is missing at '{path}' and automatic downloads are "
            f"disabled (REFACTOR_NO_AUTO_DOWNLOAD is set). Please download it manually from: {url}"
        )

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    part_path = path + ".part"
    start = os.path.getsize(part_path) if os.path.exists(part_path) else 0

    request = urllib.request.Request(url)
    if start > 0:
        request.add_header("Range", f"bytes={start}-")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        total = int(response.headers.get("Content-Length", 0)) + start
        mode = "ab" if start > 0 else "wb"
        logger.status(f"Downloading {name} -> {path}" + (f" (resuming at {start} bytes)" if start else ""))
        with open(part_path, mode) as fh, _progress(total, name, path, start) as progress:
            if start:
                progress.update(start)
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
                progress.update(len(chunk))

    size = os.path.getsize(part_path)
    if size < min_bytes:
        os.remove(part_path)
        raise IOError(f"[ReFactor] Downloaded file '{name}' is only {size} bytes (expected >= {min_bytes}) — "
                      f"the mirror probably returned an error page. Please fetch manually: {url}")
    os.replace(part_path, path)
    return path


class _progress:
    def __init__(self, total, name, path, start=0):
        self.total = total
        self.name = name
        self.path = path
        self.done = start

    def __enter__(self):
        return self

    def update(self, n):
        self.done += n
        if self.total > 0:
            pct = 100.0 * self.done / self.total
            sys_print(f"\r[ReFactor] {self.name}: {pct:5.1f}% ({self.done // 1048576}/{self.total // 1048576} MiB)")
        else:
            sys_print(f"\r[ReFactor] {self.name}: {self.done // 1048576} MiB")

    def __exit__(self, *exc):
        sys_print("\n")
        return False


def sys_print(msg):
    print(msg, end="", flush=True)
