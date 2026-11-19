#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""直播盒子 Demo 主程序.

功能:
  1. USB 摄像头采集 (V4L2 / MJPG)
  2. RTMP / SRT 推流到直播平台
  3. 本地窗口预览 (带显示屏) 或 Web MJPEG 预览 (不带显示屏)
  4. Web 控制台修改采集/推流参数

用法:
  python3 main.py                 # 自动：有显示则开本地窗口，同时提供 Web 控制台
  python3 main.py --no-ui         # 无界面（适合不带显示屏的盒子）
  python3 main.py --start         # 启动后自动开始推流
  python3 main.py --url rtmp://host/live/key
"""
import argparse
import collections
import copy
import json
import os
import signal
import socket
import subprocess
import threading
import time

from camera import CameraWorker
from streamer import Streamer, mask_url
from webui import WebServer

DEFAULTS = {
    "camera": {
        "device": "auto",
        "width": 1280,
        "height": 720,
        "fps": 15,
        "fourcc": "MJPG",
    },
    "stream": {
        "auto_start": False,
        "url": "",
        "encoder": "libx264",
        "preset": "veryfast",
        "bitrate_kbps": 2500,
        "gop_seconds": 2,
        "audio": {
            "enabled": True,
            "device": "plughw:0,0",
            "bitrate_kbps": 96,
        },
        "auto_reconnect": True,
        "retry_delay_s": 3,
    },
    "web": {
        "host": "0.0.0.0",
        "port": 8080,
        "preview_fps": 10,
        "preview_quality": 70,
    },
    "preview": {
        "enabled": True,
        "window_w": 960,
        "ui_fps": 15,
    },
}


def deep_merge(dst, src):
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            deep_merge(dst[key], value)
        else:
            dst[key] = value
    return dst


def as_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if value is None:
        return default
    return bool(value)


def load_config(path):
    cfg = copy.deepcopy(DEFAULTS)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            deep_merge(cfg, json.load(f))
    return normalize_config(cfg)


def normalize_config(cfg):
    cam = cfg.setdefault("camera", {})
    cam["device"] = str(cam.get("device", "auto")).strip() or "auto"
    cam["width"] = int(cam.get("width", 1280))
    cam["height"] = int(cam.get("height", 720))
    cam["fps"] = int(cam.get("fps", 15))
    cam["fourcc"] = str(cam.get("fourcc", "MJPG")).upper()
    cam["rotate"] = int(cam.get("rotate", 0))

    st = cfg.setdefault("stream", {})
    st["auto_start"] = as_bool(st.get("auto_start"), False)
    st["url"] = str(st.get("url") or "").strip()
    st["encoder"] = str(st.get("encoder", "libx264"))
    st["preset"] = str(st.get("preset", "veryfast"))
    st["bitrate_kbps"] = int(st.get("bitrate_kbps", 2500))
    st["gop_seconds"] = int(st.get("gop_seconds", 2))
    st["auto_reconnect"] = as_bool(st.get("auto_reconnect"), True)
    st["retry_delay_s"] = float(st.get("retry_delay_s", 3))
    audio = st.setdefault("audio", {})
    audio["enabled"] = as_bool(audio.get("enabled"), True)
    audio["device"] = str(audio.get("device", "plughw:0,0")).strip()
    audio["bitrate_kbps"] = int(audio.get("bitrate_kbps", 96))

    web = cfg.setdefault("web", {})
    web["host"] = str(web.get("host", "0.0.0.0"))
    web["port"] = int(web.get("port", 8080))
    web["preview_fps"] = int(web.get("preview_fps", 10))
    web["preview_quality"] = int(web.get("preview_quality", 70))

    pv = cfg.setdefault("preview", {})
    pv["enabled"] = as_bool(pv.get("enabled"), True)
    pv["window_w"] = int(pv.get("window_w", 960))
    pv["ui_fps"] = int(pv.get("ui_fps", 15))
    return cfg


def validate_config(cfg):
    cam = cfg["camera"]
    if not (160 <= cam["width"] <= 1920 and 160 <= cam["height"] <= 1080):
        raise ValueError("Resolution must be between 160x160 and 1920x1080")
    if cam["width"] % 2 or cam["height"] % 2:
        raise ValueError("Resolution width and height must be even numbers")
    if not (1 <= cam["fps"] <= 60):
        raise ValueError("Frame rate must be between 1 and 60")
    if cam["fourcc"] not in ("MJPG", "YUYV"):
        raise ValueError("Pixel format must be MJPG or YUYV")
    if cam["rotate"] not in (0, 90, 180, 270):
        raise ValueError("Rotation must be 0 / 90 / 180 / 270")

    st = cfg["stream"]
    url = st["url"]
    if url and not url.startswith(("rtmp://", "rtmps://", "srt://")):
        raise ValueError("Stream URL must start with rtmp://, rtmps://, or srt://")
    if st["encoder"] not in ("libx264", "h264_v4l2m2m", "mpph264enc"):
        raise ValueError("Encoder must be libx264, h264_v4l2m2m, or mpph264enc")
    if not (200 <= st["bitrate_kbps"] <= 20000):
        raise ValueError("Bitrate must be between 200 and 20000 kbps")
    if not (1 <= st["gop_seconds"] <= 10):
        raise ValueError("Keyframe interval must be between 1 and 10 seconds")
    audio = st["audio"]
    if audio["enabled"] and not audio["device"]:
        raise ValueError("Audio device is required when audio is enabled")
    if audio["enabled"] and any(
            ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                      "0123456789_:.,-"
            for ch in audio["device"]):
        raise ValueError("Audio device name contains invalid characters")

    web = cfg["web"]
    if not (1 <= web["preview_fps"] <= 30):
        raise ValueError("Web preview frame rate must be between 1 and 30")
    if not (30 <= web["preview_quality"] <= 95):
        raise ValueError("Web preview quality must be between 30 and 95")
    if not (1 <= cfg["preview"]["ui_fps"] <= 30):
        raise ValueError("Local preview frame rate must be between 1 and 30")


def get_local_ip():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def display_available():
    """Qt 在 X 连不上时会直接 abort 整个进程，这里先做无副作用探测."""
    if not os.environ.get("DISPLAY"):
        return False
    try:
        result = subprocess.run(["xdpyinfo"], capture_output=True, timeout=3)
        return result.returncode == 0
    except FileNotFoundError:
        return bool(os.environ.get("XAUTHORITY"))
    except Exception:                              # noqa: BLE001
        return False


class App:
    def __init__(self, cfg, cfg_path):
        self.cfg = cfg
        self.cfg_path = cfg_path
        self.stop_event = threading.Event()
        self.logs = collections.deque(maxlen=200)
        self._log_lock = threading.Lock()
        self._closed = threading.Event()

        self.local_ip = get_local_ip()
        port = cfg["web"]["port"]
        self.urls = [f"http://{self.local_ip}:{port}"]
        if self.local_ip != "127.0.0.1":
            self.urls.append(f"http://127.0.0.1:{port}")

        self.camera = CameraWorker(cfg["camera"], log=self.log)
        self.streamer = Streamer(cfg["stream"], self.camera, log=self.log)
        self.web = None

    # ---------------- 日志 ----------------
    def log(self, msg):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        with self._log_lock:
            self.logs.append(line)
        print(line, flush=True)

    # ---------------- 生命周期 ----------------
    def start(self):
        self.log("-" * 58)
        self.log("Live Box demo starting")
        self.camera.start()
        if self.camera.wait_ready(6):
            st = self.camera.state()
            self.log(f"[camera] {st['device']} {st['width']}x{st['height']} ready")
        else:
            self.log("[camera] Camera not ready, the web page will keep showing NO SIGNAL")

        self.web = WebServer(self, self.cfg["web"], log=self.log)
        if not self.web.start():
            self.log(f"[web] Failed to start web console on port {self.cfg['web']['port']}")

        self.log(f"[web] Preview / configuration page: {self.urls[0]}")
        if self.cfg["stream"]["auto_start"] and self.cfg["stream"]["url"]:
            ok, msg = self.start_stream()
            self.log(f"[stream] Auto-start: {msg}")

    def shutdown(self, *_):
        if self._closed.is_set():
            return
        self._closed.set()
        self.stop_event.set()
        self.log("[main] Shutting down...")
        try:
            self.streamer.stop()
        except Exception as exc:                   # noqa: BLE001
            self.log(f"[main] Error while stopping stream: {exc}")
        if self.web is not None:
            try:
                self.web.stop()
            except Exception:                      # noqa: BLE001
                pass
        try:
            self.camera.stop()
        except Exception:                          # noqa: BLE001
            pass
        self.log("[main] Exited")

    # ---------------- 推流控制 ----------------
    def start_stream(self):
        url = self.cfg["stream"]["url"]
        if not url:
            return False, "Please enter a stream URL first"
        self.streamer.update_config(self.cfg["stream"])
        ok, msg = self.streamer.start()
        if ok:
            self.log(f"[stream] Stream target {mask_url(url)}")
        else:
            self.log(f"[stream] {msg}")
        return ok, msg

    def stop_stream(self):
        self.streamer.stop()
        self.log("[stream] Streaming stopped")
        return True, "Streaming stopped"

    def toggle_stream(self):
        if self.streamer.state()["active"]:
            return self.stop_stream()
        return self.start_stream()

    # ---------------- 状态 / 配置 ----------------
    def status(self):
        return {
            "board": {"ip": self.local_ip,
                      "time": time.strftime("%Y-%m-%d %H:%M:%S")},
            "camera": self.camera.state(),
            "stream": self.streamer.state(),
            "web": {"port": self.cfg["web"]["port"],
                    "urls": self.urls,
                    "preview_fps": self.cfg["web"]["preview_fps"]},
            "logs": list(self.logs)[-40:],
        }

    def update_config(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("Invalid configuration format")
        new_cfg = copy.deepcopy(self.cfg)
        deep_merge(new_cfg, payload)
        normalize_config(new_cfg)
        validate_config(new_cfg)

        tmp = self.cfg_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(new_cfg, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.replace(tmp, self.cfg_path)
        except OSError as exc:
            self.log(f"[config] Failed to save: {exc}")
            return False, f"Failed to save configuration: {exc}"

        old = self.cfg
        cam_changed = old["camera"] != new_cfg["camera"]
        stream_changed = old["stream"] != new_cfg["stream"]
        was_active = self.streamer.state()["active"]
        self.cfg = new_cfg
        self.streamer.update_config(new_cfg["stream"])
        self.log("[config] Configuration saved")

        if cam_changed:
            self.camera.update_config(new_cfg["camera"])
            if was_active:
                self.streamer.stop()
                threading.Thread(target=self._restore_stream,
                                 name="restore-stream", daemon=True).start()
                return True, "Configuration saved; camera restarting, streaming will resume shortly", new_cfg
            return True, "Configuration saved; camera restarting", new_cfg

        if stream_changed and was_active:
            self.streamer.stop()
            self.streamer.start()
            return True, "Configuration saved; streaming restarted with new settings", new_cfg
        return True, "Configuration saved", new_cfg

    def _restore_stream(self):
        self.camera.wait_ready(8)
        ok, msg = self.streamer.start()
        self.log(f"[main] Stream resumed: {msg}")

    # ---------------- 无界面运行 ----------------
    def headless_loop(self):
        last = None
        while not self.stop_event.wait(2):
            cam = self.camera.state()
            st = self.streamer.state()
            line = (f"camera={'on' if cam['opened'] else 'off'} "
                    f"{cam['width']}x{cam['height']} {cam['fps']:.1f}fps | "
                    f"stream={st['state']} {st['kbps']:.0f}kbps")
            if line != last:
                self.log(f"[status] {line}")
                last = line


def main():
    base = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description="Live Box demo (USB camera streaming)")
    parser.add_argument("--config", default=os.path.join(base, "config.json"))
    parser.add_argument("--camera", help="Override camera device, e.g. auto / 73 / /dev/video73")
    parser.add_argument("--url", help="Override stream URL")
    parser.add_argument("--start", action="store_true", help="Start streaming automatically")
    parser.add_argument("--no-ui", action="store_true", help="Do not open the local preview window")
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.camera:
        cfg["camera"]["device"] = args.camera
    if args.url:
        cfg["stream"]["url"] = args.url
    if args.start:
        cfg["stream"]["auto_start"] = True
    cfg = normalize_config(cfg)
    try:
        validate_config(cfg)
    except ValueError as exc:
        print(f"Configuration error: {exc}")
        return 2

    app = App(cfg, args.config)
    signal.signal(signal.SIGINT, app.shutdown)
    signal.signal(signal.SIGTERM, app.shutdown)

    try:
        app.start()
        use_ui = (not args.no_ui and cfg["preview"]["enabled"]
                  and display_available())
        if use_ui:
            from ui import run_preview
            if not run_preview(app, cfg["preview"], log=app.log):
                app.headless_loop()
        else:
            if not args.no_ui and cfg["preview"]["enabled"]:
                app.log("[ui] DISPLAY not found, using web preview (no local window)")
            app.headless_loop()
    except KeyboardInterrupt:
        pass
    finally:
        app.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
