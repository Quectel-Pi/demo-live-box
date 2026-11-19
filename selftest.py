#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""板端自检：摄像头、ffmpeg、音频、5 秒本地编码.

用法: python3 selftest.py [--seconds 5]
结果全部 PASS 时退出码为 0。
"""
import argparse
import json
import os
import signal
import subprocess
import time

from camera import CameraWorker
from main import load_config
from streamer import Streamer

BASE = os.path.dirname(os.path.abspath(__file__))
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}"
          + (f"  {detail}" if detail else ""))
    return ok


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=os.path.join(BASE, "config.json"))
    parser.add_argument("--seconds", type=int, default=5)
    args = parser.parse_args()

    cfg = load_config(args.config)
    print(f"Resolution: {cfg['camera']['width']}x{cfg['camera']['height']} "
          f"@{cfg['camera']['fps']}fps {cfg['camera']['fourcc']}")
    print(f"Encoder: {cfg['stream']['encoder']}  "
          f"Bitrate: {cfg['stream']['bitrate_kbps']}kbps")

    # 1. ffmpeg
    ffmpeg_ok = subprocess.run(["which", "ffmpeg"],
                               capture_output=True).returncode == 0
    check("ffmpeg available", ffmpeg_ok)

    # 2. 编码器
    enc_name = cfg["stream"]["encoder"]
    if enc_name == "mpph264enc":
        gst_ok = subprocess.run(["gst-inspect-1.0", "mpph264enc"],
                                capture_output=True).returncode == 0
        check("Encoder mpph264enc (GStreamer)", gst_ok)
    elif ffmpeg_ok:
        enc = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                             capture_output=True, text=True).stdout
        check(f"Encoder {enc_name}", enc_name in enc)

    # 3. 摄像头
    camera = CameraWorker(cfg["camera"], log=lambda m: print("   ", m))
    camera.start()
    ready = camera.wait_ready(8)
    cam_state = camera.state()
    check("Camera opened", ready,
          f"{cam_state['device']} {cam_state['width']}x{cam_state['height']}")
    if ready:
        seq0 = camera.latest()[0]
        time.sleep(2)
        frames = camera.latest()[0] - seq0
        check("Camera frame flow", frames > 5, f"{frames} frames in 2s")
        jpg = camera.snapshot_jpeg(70)
        check("JPEG encoding", jpg is not None,
              f"{len(jpg) // 1024} KB" if jpg else "")

    # 4. 音频设备
    audio = cfg["stream"]["audio"]
    if audio["enabled"]:
        r = subprocess.run(["arecord", "-D", audio["device"], "-d", "1",
                            "-f", "S16_LE", "-r", "16000", "-c", "1",
                            "/dev/null"], capture_output=True, timeout=8)
        check(f"Audio device {audio['device']}", r.returncode == 0,
              "" if r.returncode == 0 else
              r.stderr.decode("utf-8", "ignore").strip()[-160:])

    # 5. 本地编码 (与推流同一条 ffmpeg 管线，只是输出到文件)
    if ready:
        records = os.path.join(BASE, "records")
        os.makedirs(records, exist_ok=True)
        out = os.path.join(records, "selftest.flv")
        if os.path.exists(out):
            os.remove(out)

        streamer = Streamer(cfg["stream"], camera,
                            log=lambda m: print("   ", m))
        width = camera.state()["width"]
        height = camera.state()["height"]
        cmd = streamer.build_cmd(width, height, url=out, fmt="flv")
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, bufsize=0)
        print(f"    Recording {args.seconds}s to records/selftest.flv ...")
        interval = 1.0 / max(1, cfg["camera"]["fps"])
        end = time.monotonic() + args.seconds
        last = None
        sent = 0
        try:
            while time.monotonic() < end and proc.poll() is None:
                seq, frame = camera.latest()
                if frame is not None:
                    last = frame
                if last is not None:
                    try:
                        proc.stdin.write(last.data)
                        sent += 1
                    except (BrokenPipeError, OSError):
                        break
                time.sleep(interval)
        finally:
            try:
                proc.stdin.close()
            except Exception:                      # noqa: BLE001
                pass
            # 音频输入会让 ffmpeg 一直运行，用 SIGINT 让它正常收尾并写完 FLV
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=5)
            except Exception:                      # noqa: BLE001
                proc.kill()
                proc.wait(timeout=2)
        ok = os.path.exists(out) and os.path.getsize(out) > 100 * 1024
        size_kb = os.path.getsize(out) // 1024 if os.path.exists(out) else 0
        err = proc.stderr.read().decode("utf-8", "ignore").strip()
        check("H.264 encoding", ok, f"{sent} frames, {size_kb} KB"
              + (f", {err[-160:]}" if not ok and err else ""))

        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_name,width,height,avg_frame_rate,duration",
             "-of", "json", out],
            capture_output=True, text=True)
        if ok and probe.returncode == 0:
            info = json.loads(probe.stdout or "{}").get("streams", [])
            streams = ", ".join(
                f"{s.get('codec_name')} {s.get('width')}x{s.get('height')} "
                f"{s.get('avg_frame_rate')}" for s in info)
            check("Output stream info", bool(info), streams)

    camera.stop()

    failed = [r for r in RESULTS if not r[1]]
    print("-" * 50)
    print(f"Total {len(RESULTS)} checks, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
