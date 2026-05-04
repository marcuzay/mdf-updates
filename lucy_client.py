"""
lucy_client.py — Full Body Swap for MDF 2026
Place at: app/processors/lucy_client.py

Runs decart_worker.py in a SEPARATE Python process to avoid numpy conflicts.
MDF's portable Python stays on numpy 1.x. The worker Python has numpy 2.x + decart.

Place decart_worker.py in the MDF 2026 root folder.
Install decart in a separate Python (not MDF's portable Python):
    python -m pip install decart aiortc av opencv-python httpx
"""

import subprocess
import threading
import json
import os
import base64
import time
import sys
import os
import traceback
from pathlib import Path
from typing import Optional, Callable

import cv2
import numpy as np

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

HEARTBEAT_INTERVAL = 28

# Permanent URL to your config file — this NEVER changes even when you change VPS
# Host this on GitHub raw, Dropbox, Google Drive, or any permanent URL
LICENCE_SERVER_URL = "http://16.170.215.170:8000"

# Path to decart_worker.py — placed in MDF 2026 root
WORKER_SCRIPT = str(Path(__file__).resolve().parents[2] / "decart_worker.py")

# Config file stores the system Python path — set once by user via UI
CONFIG_FILE = str(Path(__file__).resolve().parents[2] / "fbs_config.json")

def _load_config() -> dict:
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}

def _save_config(data: dict):
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def _find_worker_python() -> str:
    """Return the configured system Python path, or empty string if not set."""
    cfg = _load_config()
    return cfg.get("python_path", "")

def _verify_python(python_path: str) -> bool:
    """Check that the given Python path has decart installed."""
    try:
        result = subprocess.run(
            [python_path, "-c", "import decart; print('ok')"],
            capture_output=True, text=True, timeout=8,
            env={**os.environ, "PYTHONPATH": ""}  # Clear PYTHONPATH to avoid MDF contamination
        )
        return result.stdout.strip() == "ok"
    except Exception:
        return False

AIORTC_AVAILABLE = True  # Always True — we don't need it in this process


class LucyClient:
    """
    Manages Full Body Swap by spawning decart_worker.py in a separate Python
    process, communicating via stdin/stdout JSON lines.
    """

    def __init__(self, licence_token: str):
        self._licence_token = licence_token
        self._client_token: Optional[str] = None
        self._connected     = False

        self._proc:   Optional[subprocess.Popen] = None
        self._reader: Optional[threading.Thread] = None

        self._out_lock  = threading.Lock()
        self._out_frame: Optional[np.ndarray] = None

        self._connect_event = threading.Event()
        self._connect_error: Optional[str] = None
        self._on_status: Optional[Callable] = None

        self._heartbeat_thread: Optional[threading.Thread] = None
        self._heartbeat_stop    = threading.Event()
        self._stop_lock         = threading.Lock()

        self.credits_remaining: int = 0
        self.seconds_used:      int = 0

    def _status(self, msg: str, color: str = '#f0a500'):
        print(f"[FullBodySwap] {msg}")
        if self._on_status:
            try: self._on_status(msg, color)
            except Exception: pass

    def start(
        self,
        reference_image_path: str,
        prompt: str = "",
        on_status: Optional[Callable] = None,
    ) -> bool:
        if self._connected:
            return True

        self._on_status = on_status
        self._connect_event.clear()
        self._connect_error = None

        # Step 1: Get client token from licence server
        self._status("Authenticating licence...")
        try:
            import httpx
            r = httpx.post(
                f"{LICENCE_SERVER_URL}/token",
                json={"licence_token": self._licence_token},
                timeout=10,
            )
            if r.status_code == 402:
                self._status("✗ No credits remaining.", '#e05252')
                return False
            if r.status_code == 403:
                detail = r.json().get("detail", "Licence rejected.")
                self._status(f"✗ {detail}", '#e05252')
                return False
            if r.status_code != 200:
                self._status(f"✗ Licence server error ({r.status_code})", '#e05252')
                return False
            data = r.json()
            self._client_token     = data["client_token"]
            self.credits_remaining = data.get("credits_remaining", 0)
            print(f"[FullBodySwap] Token obtained. Credits: {self.credits_remaining}s")
        except Exception as e:
            self._status(f"✗ Licence server unreachable: {e}", '#e05252')
            return False

        # Step 2: Launch worker process
        if not Path(WORKER_SCRIPT).exists():
            self._status(f"✗ decart_worker.py not found at:\n{WORKER_SCRIPT}", '#e05252')
            return False

        worker_python = _find_worker_python()
        if not worker_python:
            self._status("⚠ Python path not configured. Set it in Full Body Swap settings.", '#e05252')
            return False

        if not _verify_python(worker_python):
            self._status("⚠ Selected Python doesn't have Decart installed. Run lucy_install.bat with that Python.", '#e05252')
            return False

        print(f"[FullBodySwap] Using Python: {worker_python}")
        self._status("Launching Decart worker...")

        try:
            self._proc = subprocess.Popen(
                [worker_python, WORKER_SCRIPT],
                env={**os.environ, "PYTHONPATH": ""},
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
                bufsize=0,
            )
        except Exception as e:
            self._status(f"✗ Worker launch failed: {e}", '#e05252')
            return False

        # Step 3: Start reader threads (stdout + stderr)
        self._reader = threading.Thread(
            target=self._read_loop, daemon=True, name="FBSReader"
        )
        self._reader.start()

        self._stderr_reader = threading.Thread(
            target=self._read_stderr_loop, daemon=True, name="FBSStderr"
        )
        self._stderr_reader.start()

        # Step 4: Send connect command
        self._send({
            "cmd":        "connect",
            "token":      self._client_token,
            "image_path": reference_image_path,
            "prompt":     prompt,
        })

        # Step 5: Wait for connected signal
        ok = self._connect_event.wait(timeout=35)
        if not ok or self._connect_error:
            self._status(f"✗ {self._connect_error or 'Connection timed out.'}", '#e05252')
            self.stop()
            return False

        self._connected = True
        self._start_heartbeat()
        self._status("● Active", '#4caf50')
        return True

    def stop(self):
        with self._stop_lock:
            if not self._proc:
                return
            self._connected = False
            self._heartbeat_stop.set()
            try:
                self._send({"cmd": "stop"})
                self._proc.stdin.close()
                self._proc.wait(timeout=3)
            except Exception:
                try: self._proc.kill()
                except Exception: pass
            self._proc = None

        # Notify licence server
        try:
            import httpx
            httpx.post(
                f"{LICENCE_SERVER_URL}/endsession",
                json={"licence_token": self._licence_token},
                timeout=3,
            )
        except Exception:
            pass
        print("[FullBodySwap] Stopped.")

    def process_frame(self, bgr: np.ndarray) -> np.ndarray:
        """Send webcam frame to worker, return latest transformed frame."""
        if not self._connected or not self._proc:
            return bgr
        try:
            h, w = bgr.shape[:2]
            raw  = base64.b64encode(bgr.tobytes()).decode()
            self._send({"cmd": "frame", "data": raw, "w": w, "h": h})
        except Exception:
            pass
        with self._out_lock:
            if self._out_frame is not None:
                return self._out_frame.copy()
        return bgr

        return bgr

    def is_connected(self) -> bool:
        return self._connected

    def has_output(self) -> bool:
        """True once Decart has sent at least one transformed frame back."""
        with self._out_lock:
            return self._out_frame is not None

    def update_reference(self, reference_image_path: str, prompt: str = ""):
        if not self._connected:
            return
        self._send({
            "cmd":        "update_ref",
            "image_path": reference_image_path,
            "prompt":     prompt,
        })

    def _send(self, obj: dict):
        if self._proc and self._proc.stdin:
            try:
                line = (json.dumps(obj) + "\n").encode()
                self._proc.stdin.write(line)
                self._proc.stdin.flush()
            except Exception:
                pass

    def _read_stderr_loop(self):
        """Read worker stderr in real-time so errors appear immediately."""
        while self._proc and self._proc.poll() is None:
            try:
                line = self._proc.stderr.readline()
                if not line:
                    break
                text = line.decode(errors='replace').rstrip()
                if text:
                    print(f"[Worker] {text}")
                    # Only signal fatal errors — ignore cv2/MSMF warnings
                    if not self._connected and not self._connect_event.is_set():
                        is_fatal = (
                            "Traceback (most recent call last)" in text or
                            "RuntimeError:" in text or
                            "ModuleNotFoundError:" in text or
                            "ImportError:" in text or
                            "NameError:" in text or
                            "KeyError:" in text
                        )
                        if is_fatal:
                            self._connect_error = text
                            self._connect_event.set()
            except Exception:
                break

    def _read_loop(self):
        """Read JSON messages from worker stdout."""
        while self._proc and self._proc.poll() is None:
            try:
                line = self._proc.stdout.readline()
                if not line:
                    break
                msg = json.loads(line.decode().strip())
            except Exception:
                continue

            t = msg.get("type")

            if t == "status":
                self._status(msg.get("msg", ""), msg.get("color", '#f0a500'))

            elif t == "connected":
                self._connect_event.set()

            elif t == "error":
                self._connect_error = msg.get("msg", "Unknown error")
                self._connect_event.set()

            elif t == "frame":
                try:
                    raw  = base64.b64decode(msg["data"])
                    arr  = np.frombuffer(raw, dtype=np.uint8)
                    bgr  = arr.reshape((msg["h"], msg["w"], 3))
                    with self._out_lock:
                        self._out_frame = bgr.copy()
                except Exception:
                    pass

            elif t == "token_expired":
                # Client token expired — get a fresh one and restart worker
                print("[FullBodySwap] Token expired — refreshing...")
                self._status("Refreshing connection...", '#f0a500')
                threading.Thread(
                    target=self._refresh_and_restart,
                    daemon=True, name="FBSRefresh"
                ).start()

            elif t == "stopped":
                self._connected = False
                break

        print("[FullBodySwap] Worker stdout loop ended.")

    def _refresh_and_restart(self):
        """Get a fresh client token and restart the worker process."""
        import httpx
        try:
            r = httpx.post(
                f"{LICENCE_SERVER_URL}/token",
                json={"licence_token": self._licence_token},
                timeout=10,
            )
            if r.status_code != 200:
                self._status("✗ Token refresh failed.", '#e05252')
                self._connected = False
                return
            data = r.json()
            new_token = data["client_token"]
            self.credits_remaining = data.get("credits_remaining", 0)
            print(f"[FullBodySwap] Fresh token obtained.")
            # Send new token to worker
            self._send({
                "cmd":   "refresh_token",
                "token": new_token,
            })
            self._status("● Active", '#4caf50')
        except Exception as e:
            self._status(f"✗ Refresh error: {e}", '#e05252')
            self._connected = False

    def _start_heartbeat(self):
        self._heartbeat_stop.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True, name="FBSHeartbeat"
        )
        self._heartbeat_thread.start()

    def _heartbeat_loop(self):
        import httpx
        last = time.time()
        while not self._heartbeat_stop.wait(timeout=1):
            if time.time() - last < HEARTBEAT_INTERVAL:
                continue
            last = time.time()
            try:
                r = httpx.post(
                    f"{LICENCE_SERVER_URL}/heartbeat",
                    json={"licence_token":   self._licence_token,
                          "elapsed_seconds": HEARTBEAT_INTERVAL},
                    timeout=5,
                )
                if r.status_code == 200:
                    data = r.json()
                    self.credits_remaining = data.get("credits_remaining", 0)
                    self.seconds_used      = data.get("seconds_used", 0)
                    if not data.get("active", True):
                        self._status(f"✗ {data.get('reason', 'Session ended.')}", '#e05252')
                        self._connected = False
                        self.stop()
                        break
                    else:
                        rem = self.credits_remaining
                        self._status(
                            f"⚠ {rem}s remaining" if rem < 120 else "● Active",
                            '#f0a500' if rem < 120 else '#4caf50'
                        )
            except Exception as e:
                print(f"[FullBodySwap] Heartbeat error: {e}")
