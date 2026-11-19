#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""USB 摄像头采集 (V4L2).

- 默认自动定位 UVC 摄像头 (/dev/v4l/by-id/*usb*-video-index0)
- 优先 MJPG，降低 USB 带宽与解码压力
- 单实例独占设备：本地预览 / Web 预览 / 推流共用同一帧缓存
"""
import glob
import os
import threading
import time

import cv2

# 板载 ISP / CIF / VPSS 节点不是 UVC 摄像头，自动探测时跳过
_SKIP_NAMES = ("rkisp", "rkcif", "rkvpss", "statistics", "rkaiq", "dummy")


def resolve_device(device):
    """把配置中的 auto / 序号 / 路径解析成可打开的 V4L2 节点."""
    device = str(device or "auto").strip()
    if device.isdigit():
        return f"/dev/video{device}"
    if device and device != "auto":
        return device

    for path in sorted(glob.glob("/dev/v4l/by-id/*-video-index0")):
        if "usb" in os.path.basename(path).lower():
            return os.path.realpath(path)

    candidates = []
    for sysdir in glob.glob("/sys/class/video4linux/video*"):
        try:
            with open(os.path.join(sysdir, "name"),
                      encoding="utf-8", errors="ignore") as f:
                name = f.read().strip()
        except OSError:
            continue
        if any(k in name.lower() for k in _SKIP_NAMES):
            continue
        index = int(os.path.basename(sysdir)[5:])
        candidates.append((index, f"/dev/video{index}"))
    if candidates:
        candidates.sort()
        return candidates[0][1]
    return "/dev/video0"


class CameraWorker(threading.Thread):
    """后台采集线程，始终只保留最新一帧."""

    def __init__(self, cfg, log=None):
        super().__init__(name="camera", daemon=True)
        self._log = log or (lambda msg: None)
        self._cfg_lock = threading.Lock()
        self._cfg = self._normalize(cfg or {})
        self._stop = threading.Event()
        self._restart = threading.Event()
        self._lock = threading.Lock()
        self._jpeg_lock = threading.Lock()
        self._frame = None
        self._seq = 0
        self._frame_time = 0.0
        self._jpeg_cache = {}
        self._state = {
            "opened": False, "device": "", "width": 0, "height": 0,
            "fps": 0.0, "frames": 0, "error": "",
        }

    # ---------------- 配置 ----------------
    @staticmethod
    def _normalize(cfg):
        return {
            "device": str(cfg.get("device", "auto")),
            "width": int(cfg.get("width", 1280)),
            "height": int(cfg.get("height", 720)),
            "fps": int(cfg.get("fps", 15)),
            "fourcc": str(cfg.get("fourcc", "MJPG")).upper(),
            "rotate": int(cfg.get("rotate", 0)),
        }

    @staticmethod
    def _rotate(frame, degrees):
        if degrees == 90:
            return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        if degrees == 180:
            return cv2.rotate(frame, cv2.ROTATE_180)
        if degrees == 270:
            return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
        return frame

    def update_config(self, cfg):
        new_cfg = self._normalize(cfg)
        with self._cfg_lock:
            if new_cfg == self._cfg:
                return False
            self._cfg = new_cfg
        self._restart.set()          # 让采集线程重新打开设备
        return True

    def config(self):
        with self._cfg_lock:
            return dict(self._cfg)

    # ---------------- 状态 / 数据 ----------------
    def state(self):
        with self._lock:
            return dict(self._state)

    def latest(self):
        """返回 (seq, frame)。frame 为只读引用，调用方不要原地修改."""
        with self._lock:
            return self._seq, self._frame

    def snapshot_jpeg(self, quality=70):
        """给 Web MJPEG 预览用，同一帧相同质量只编码一次."""
        with self._lock:
            seq, frame = self._seq, self._frame
        if frame is None:
            return None
        with self._jpeg_lock:
            cached = self._jpeg_cache.get(quality)
            if cached and cached[0] == seq:
                return cached[1]
            ok, buf = cv2.imencode(
                ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
            if not ok:
                return None
            data = buf.tobytes()
            self._jpeg_cache[quality] = (seq, data)
            return data

    def wait_ready(self, timeout=6.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.state()["opened"] and self.state()["width"] > 0:
                return True
            time.sleep(0.1)
        return False

    # ---------------- 生命周期 ----------------
    def stop(self):
        self._stop.set()
        if self.is_alive():
            self.join(timeout=3.0)

    def run(self):
        while not self._stop.is_set():
            cfg = self.config()
            cap = self._open(cfg)
            if cap is None:
                if self._stop.wait(2.0):
                    break
                continue

            failures = 0
            window_start = time.monotonic()
            window_frames = 0
            rotate = cfg.get("rotate", 0)
            try:
                while not self._stop.is_set() and not self._restart.is_set():
                    ok, frame = cap.read()
                    if not ok:
                        failures += 1
                        if failures >= 15:
                            self._log("[camera] Continuous read failures, reopening device")
                            break
                        time.sleep(0.03)
                        continue

                    failures = 0
                    frame = self._rotate(frame, rotate)
                    window_frames += 1
                    now = time.monotonic()
                    with self._lock:
                        self._seq += 1
                        self._frame = frame
                        self._frame_time = time.time()
                        self._state["frames"] = self._seq
                    if now - window_start >= 1.0:
                        with self._lock:
                            self._state["fps"] = window_frames / (now - window_start)
                        window_start, window_frames = now, 0
            finally:
                cap.release()
                with self._lock:
                    self._state["opened"] = False
                    self._state["fps"] = 0.0
            self._restart.clear()

    # ---------------- 打开设备 ----------------
    def _open(self, cfg):
        device = resolve_device(cfg["device"])
        try:
            cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
        except Exception as exc:                    # noqa: BLE001
            cap = None
            self._set_error(f"Error opening {device}: {exc}")
            return None

        if not cap.isOpened():
            self._set_error(f"Unable to open camera {device}")
            self._log(f"[camera] Failed to open: {device}")
            return None

        fourcc = cfg.get("fourcc") or "MJPG"
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg["width"])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg["height"])
        cap.set(cv2.CAP_PROP_FPS, cfg["fps"])
        # 尽量缩小驱动内部缓冲，降低预览延迟
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:                          # noqa: BLE001
            pass

        ok, frame = cap.read()
        if not ok or frame is None:
            cap.release()
            self._set_error(f"Camera {device} produced no frames")
            self._log(f"[camera] {device} produced no frames")
            return None

        frame = self._rotate(frame, cfg.get("rotate", 0))
        h, w = frame.shape[:2]
        with self._lock:
            self._state.update(opened=True, device=device, width=w,
                               height=h, error="")
            self._state["fps"] = 0.0
            self._seq += 1
            self._frame = frame
            self._frame_time = time.time()
        self._log(f"[camera] {device} opened: {w}x{h} "
                  f"(requested {cfg['width']}x{cfg['height']}@{cfg['fps']} {fourcc})")
        return cap

    def _set_error(self, msg):
        with self._lock:
            self._state.update(opened=False, error=msg)
