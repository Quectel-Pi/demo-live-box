#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RTMP / SRT 推流模块.

Python 侧独占摄像头，按固定帧率把 BGR 原始帧补给编码器：
  - libx264: 通过管道喂给 ffmpeg（软编，默认）
  - mpph264enc: 通过 GStreamer appsrc 送入 Rockchip MPP 硬编（RK35xx）
这样本地预览、Web 预览、推流三路共用同一个摄像头，不会出现设备被重复打开。
"""
import threading
import time
import subprocess
import re

try:
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    _GST_ERROR = ""
except Exception as exc:                        # noqa: BLE001
    Gst = None
    _GST_ERROR = str(exc)

_VIDEO_ENCODERS = ("libx264", "h264_v4l2m2m", "mpph264enc")
_URL_RE = re.compile(r"(?:rtmps?|srt)://[^\s\"']+")


def mask_text(text):
    """把文本中的推流地址统一打码，避免密钥出现在日志里."""
    return _URL_RE.sub(lambda m: mask_url(m.group(0)), str(text))


def mask_url(url):
    """日志里隐去推流密钥."""
    url = (url or "").strip()
    if "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    if "/" in rest:
        host = rest.split("/", 1)[0]
        return f"{scheme}://{host}/******"
    return f"{scheme}://******"


class Streamer:
    def __init__(self, cfg, camera, log=None):
        self._log = log or (lambda msg: None)
        self.camera = camera
        self._cfg = self._normalize(cfg or {})
        self._stop = threading.Event()
        self._thread = None
        self._proc = None
        self._proc_lock = threading.Lock()
        self._lock = threading.Lock()
        self._gst_lock = threading.Lock()
        self._gst_bytes = 0
        if Gst is not None:
            Gst.init(None)
        self._state = {
            "active": False, "state": "idle", "url": "",
            "url_configured": bool(self._cfg["url"]),
            "kbps": 0.0, "sent_fps": 0.0, "uptime_s": 0,
            "last_error": "", "restarts": 0,
        }

    # ---------------- 配置 ----------------
    @staticmethod
    def _normalize(cfg):
        audio = cfg.get("audio") or {}
        return {
            "auto_start": bool(cfg.get("auto_start", False)),
            "url": str(cfg.get("url", "")).strip(),
            "encoder": str(cfg.get("encoder", "libx264")),
            "preset": str(cfg.get("preset", "veryfast")),
            "bitrate_kbps": int(cfg.get("bitrate_kbps", 2500)),
            "gop_seconds": int(cfg.get("gop_seconds", 2)),
            "audio": {
                "enabled": bool(audio.get("enabled", False)),
                "device": str(audio.get("device", "plughw:0,0")),
                "bitrate_kbps": int(audio.get("bitrate_kbps", 96)),
            },
            "auto_reconnect": bool(cfg.get("auto_reconnect", True)),
            "retry_delay_s": float(cfg.get("retry_delay_s", 3)),
        }

    def update_config(self, cfg):
        with self._lock:
            self._cfg = self._normalize(cfg)
            self._state["url_configured"] = bool(self._cfg["url"])

    def config(self):
        with self._lock:
            return dict(self._cfg)

    def state(self):
        with self._lock:
            st = dict(self._state)
        st["url"] = mask_url(st.get("url", ""))
        return st

    # ---------------- 命令拼装 ----------------
    def build_cmd(self, width, height, url=None, fmt=None):
        cfg = self.config()
        url = (url or cfg["url"]).strip()
        if not url:
            raise ValueError("未配置推流地址")
        if fmt is None:
            fmt = "flv" if url.startswith(("rtmp://", "rtmps://")) else "mpegts"

        fps = max(1, int(self.camera.config().get("fps", 15)))
        bitrate = max(200, int(cfg["bitrate_kbps"]))
        gop = max(1, int(fps * max(1, cfg["gop_seconds"])))

        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin",
            "-progress", "pipe:1", "-stats_period", "1",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{width}x{height}", "-framerate", str(fps),
            "-i", "pipe:0",
        ]

        audio = cfg["audio"]
        if audio["enabled"]:
            cmd += ["-f", "alsa", "-thread_queue_size", "1024",
                    "-i", audio["device"]]
            cmd += ["-map", "0:v:0", "-map", "1:a:0"]

        encoder = cfg["encoder"]
        # mpph264enc 由 GStreamer 后端处理；走到 ffmpeg 时一律用软编
        if encoder == "mpph264enc" or encoder not in _VIDEO_ENCODERS:
            encoder = "libx264"
        cmd += ["-c:v", encoder]
        if encoder == "libx264":
            cmd += ["-preset", cfg["preset"], "-tune", "zerolatency",
                    "-profile:v", "baseline"]
        cmd += [
            "-pix_fmt", "yuv420p",
            "-b:v", f"{bitrate}k", "-maxrate", f"{bitrate}k",
            "-bufsize", f"{bitrate * 2}k", "-g", str(gop),
            "-r", str(fps),
        ]

        if audio["enabled"]:
            cmd += ["-c:a", "aac", "-b:a", f"{audio['bitrate_kbps']}k",
                    "-ar", "44100", "-ac", "2"]
        else:
            cmd += ["-an"]

        cmd += ["-f", fmt, url]
        return cmd

    # ---------------- 生命周期 ----------------
    def start(self):
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        if self._thread and self._thread.is_alive():
            return False, "上一路推流尚未退出"
        cfg = self.config()
        if not cfg["url"]:
            return False, "请先填写推流地址"
        self._stop.clear()
        with self._lock:
            self._state.update(active=True, state="connecting",
                               last_error="", kbps=0.0, sent_fps=0.0,
                               uptime_s=0, url=cfg["url"], restarts=0)
        self._thread = threading.Thread(target=self._loop, name="streamer",
                                        daemon=True)
        self._thread.start()
        self._log(f"[stream] 开始推流 -> {mask_url(cfg['url'])}")
        return True, "开始推流"

    def stop(self):
        self._stop.set()
        with self._proc_lock:
            proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                if proc.stdin:
                    proc.stdin.close()
            except Exception:                       # noqa: BLE001
                pass
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except Exception:                       # noqa: BLE001
                proc.kill()
        if self._thread and self._thread.is_alive() \
                and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)
        with self._lock:
            self._state.update(active=False, state="idle", kbps=0.0,
                               sent_fps=0.0, uptime_s=0)

    def toggle(self):
        if self.state()["active"]:
            self.stop()
            return True, "已停止推流"
        return self.start()

    # ---------------- 内部循环 ----------------
    def _loop(self):
        while not self._stop.is_set():
            self._set(state="connecting")
            try:
                self._run_once()
            except Exception as exc:                # noqa: BLE001
                self._last_error(mask_text(exc))
                self._log(f"[stream] {mask_text(exc)}")
            if self._stop.is_set():
                break
            if not self.config()["auto_reconnect"]:
                self._set(state="error", active=False)
                return
            delay = max(0.5, self.config()["retry_delay_s"])
            self._set(state="retrying")
            self._log(f"[stream] {delay:.0f}s 后重连")
            if self._stop.wait(delay):
                break
        self._set(active=False, state="idle")

    def _run_once(self):
        cam = self.camera.state()
        width, height = int(cam["width"]), int(cam["height"])
        if width <= 0 or height <= 0:
            raise RuntimeError("摄像头未就绪，无法推流")

        if self.config()["encoder"] == "mpph264enc":
            if Gst is None:
                self._log(f"[stream] GStreamer 不可用（{_GST_ERROR}），"
                          "回退 libx264 软编")
            else:
                return self._run_gst(width, height)

        cmd = self.build_cmd(width, height)
        self._log("[stream] " + " ".join(
            mask_url(a) if a.startswith(("rtmp", "srt")) else a for a in cmd))

        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, bufsize=0)
        with self._proc_lock:
            self._proc = proc

        reader = threading.Thread(target=self._read_stderr, args=(proc,),
                                  name="ffmpeg-log", daemon=True)
        reader.start()
        progress = threading.Thread(target=self._read_progress, args=(proc,),
                                    name="ffmpeg-progress", daemon=True)
        progress.start()

        fps = max(1, int(self.camera.config().get("fps", 15)))
        interval = 1.0 / fps
        next_tick = time.monotonic()
        started = time.monotonic()
        last_seq = -1
        last_frame = None
        sent = 0
        window_start = time.monotonic()
        window_frames = 0
        first_frame = True

        try:
            while not self._stop.is_set() and proc.poll() is None:
                seq, frame = self.camera.latest()
                if frame is None:
                    time.sleep(0.02)
                    continue
                if frame.shape[1] != width or frame.shape[0] != height:
                    raise RuntimeError("采集分辨率变化，需重新建立推流")

                # 采集帧率低于推流帧率时复用上一帧，保证 ffmpeg 输入节奏稳定
                if seq != last_seq:
                    last_frame, last_seq = frame, seq
                if last_frame is None:
                    time.sleep(0.01)
                    continue

                try:
                    proc.stdin.write(last_frame.data)
                except (BrokenPipeError, OSError):
                    break

                sent += 1
                window_frames += 1
                if first_frame:
                    first_frame = False
                    self._set(state="live")

                now = time.monotonic()
                if now - window_start >= 1.0:
                    elapsed = now - window_start
                    self._set(sent_fps=round(window_frames / elapsed, 1),
                              uptime_s=int(now - started))
                    window_start, window_frames = now, 0

                next_tick += interval
                delay = next_tick - time.monotonic()
                if delay > 0:
                    if self._stop.wait(delay):
                        break
                elif delay < -0.3:
                    next_tick = time.monotonic()   # 落后太多，重新对齐
        finally:
            try:
                if proc.stdin:
                    proc.stdin.close()
            except Exception:                       # noqa: BLE001
                pass
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except Exception:                   # noqa: BLE001
                    proc.kill()
            with self._proc_lock:
                self._proc = None
            reader.join(timeout=1)
            progress.join(timeout=1)
            if self._stop.is_set():
                self._log(f"[stream] 已停止，共发送 {sent} 帧")
            else:
                self._log(f"[stream] ffmpeg 退出 code={proc.returncode}")

    # ---------------- GStreamer / Rockchip MPP 硬编 ----------------
    def _gst_desc(self, width, height):
        cfg = self.config()
        fps = max(1, int(self.camera.config().get("fps", 15)))
        bps = max(200, int(cfg["bitrate_kbps"])) * 1000
        gop = max(1, int(fps * max(1, cfg["gop_seconds"])))
        audio = cfg["audio"]

        desc = (
            "appsrc name=src is-live=true do-timestamp=true block=false "
            "format=time max-buffers=2 leaky-type=downstream "
            f"caps=video/x-raw,format=BGR,width={width},height={height},"
            f"framerate={fps}/1 ! videoconvert ! video/x-raw,format=NV12 ! "
            f"mpph264enc name=enc bps={bps} rc-mode=cbr gop={gop} "
            "profile=baseline ! h264parse config-interval=-1 ! mux. "
        )
        if audio["enabled"]:
            desc += (
                "alsasrc name=asrc do-timestamp=true ! audioconvert ! "
                "audioresample ! audio/x-raw,rate=44100,channels=2 ! "
                f"voaacenc bitrate={audio['bitrate_kbps'] * 1000} ! "
                "aacparse ! mux. "
            )
        desc += "flvmux name=mux streamable=true ! rtmpsink name=sink sync=false"
        return desc

    def _run_gst(self, width, height):
        cfg = self.config()
        self._log(f"[stream] GStreamer 硬编 mpph264enc "
                  f"{cfg['bitrate_kbps']}kbps -> {mask_url(cfg['url'])}")

        pipeline = Gst.parse_launch(self._gst_desc(width, height))
        src = pipeline.get_by_name("src")
        sink = pipeline.get_by_name("sink")
        mux = pipeline.get_by_name("mux")
        sink.set_property("location", cfg["url"])
        if cfg["audio"]["enabled"]:
            pipeline.get_by_name("asrc").set_property(
                "device", cfg["audio"]["device"])

        with self._gst_lock:
            self._gst_bytes = 0
        self._gst_live = False

        def on_buffer(_pad, info):
            buf = info.get_buffer()
            if buf is not None:
                with self._gst_lock:
                    self._gst_bytes += buf.get_size()
                if not self._gst_live:
                    self._gst_live = True
                    self._set(state="live")
            return Gst.PadProbeReturn.OK

        mux.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER,
                                            on_buffer)
        bus = pipeline.get_bus()

        # 直播管线要等首个 buffer 才完成 preroll，不能在这里阻塞等 PLAYING
        ret = pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            pipeline.set_state(Gst.State.NULL)
            raise RuntimeError("GStreamer 管道启动失败"
                               "（检查 mpph264enc / rtmpsink）")

        fps = max(1, int(self.camera.config().get("fps", 15)))
        interval = 1.0 / fps
        next_tick = time.monotonic()
        started = time.monotonic()
        window_start = started
        window_frames = 0
        last_seq = -1
        last_frame = None
        sent = 0

        try:
            while not self._stop.is_set():
                msg = bus.timed_pop_filtered(
                    0, Gst.MessageType.ERROR | Gst.MessageType.EOS)
                if msg is not None:
                    if msg.type == Gst.MessageType.ERROR:
                        err, debug = msg.parse_error()
                        raise RuntimeError(mask_text(
                            f"{err.message} {debug}".strip()))
                    raise RuntimeError("GStreamer 管道已结束")

                seq, frame = self.camera.latest()
                if frame is None:
                    time.sleep(0.02)
                    continue
                if frame.shape[1] != width or frame.shape[0] != height:
                    raise RuntimeError("采集分辨率变化，需重新建立推流")

                if seq != last_seq:
                    last_frame, last_seq = frame, seq
                if last_frame is None:
                    time.sleep(0.01)
                    continue

                buf = Gst.Buffer.new_wrapped(last_frame.tobytes())
                buf.duration = int(Gst.SECOND / fps)
                flow = src.emit("push-buffer", buf)
                if flow == Gst.FlowReturn.FLUSHING:
                    # 管线还在从 PAUSED 切到 PLAYING，稍后重试
                    time.sleep(0.01)
                elif flow != Gst.FlowReturn.OK:
                    raise RuntimeError(f"GStreamer push-buffer 返回 {flow}")
                else:
                    sent += 1
                    window_frames += 1

                now = time.monotonic()
                if now - window_start >= 1.0:
                    elapsed = now - window_start
                    with self._gst_lock:
                        nbytes, self._gst_bytes = self._gst_bytes, 0
                    self._set(kbps=round(nbytes * 8 / 1000 / elapsed, 1),
                              sent_fps=round(window_frames / elapsed, 1),
                              uptime_s=int(now - started))
                    window_start, window_frames = now, 0

                next_tick += interval
                delay = next_tick - time.monotonic()
                if delay > 0:
                    if self._stop.wait(delay):
                        break
                elif delay < -0.3:
                    next_tick = time.monotonic()
        finally:
            pipeline.set_state(Gst.State.NULL)
            if self._stop.is_set():
                self._log(f"[stream] 已停止，共发送 {sent} 帧")
            else:
                self._log(f"[stream] GStreamer 退出，共发送 {sent} 帧")

    def _read_progress(self, proc):
        """解析 ffmpeg -progress 输出，拿到真实编码码率."""
        try:
            for raw in proc.stdout:
                line = raw.decode("utf-8", errors="ignore").strip()
                if not line.startswith("bitrate="):
                    continue
                value = line.split("=", 1)[1].replace("kbits/s", "").strip()
                try:
                    self._set(kbps=round(float(value), 1))
                except ValueError:
                    pass
        except Exception:                           # noqa: BLE001
            pass

    def _read_stderr(self, proc):
        lines = 0
        last = ""
        try:
            for raw in proc.stderr:
                line = mask_text(
                    raw.decode("utf-8", errors="ignore").strip())
                if not line:
                    continue
                if "Immediate exit requested" in line:
                    continue
                last = line
                if lines < 12:
                    self._log(f"[ffmpeg] {line}")
                    lines += 1
        except Exception:                           # noqa: BLE001
            pass
        if last and not self._stop.is_set():
            self._last_error(last)

    def _set(self, **kwargs):
        with self._lock:
            self._state.update(kwargs)

    def _last_error(self, msg):
        with self._lock:
            self._state["last_error"] = msg[-300:]
