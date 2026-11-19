#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地预览窗口 (OpenCV HighGUI，带显示屏时使用).

按键: S = 开始/停止推流, R = 打印 Web 地址, Q/ESC = 退出
"""
import time

import cv2

_FONT = cv2.FONT_HERSHEY_SIMPLEX


def _text(img, text, org, scale=0.5, color=(240, 240, 240), thick=1):
    """带黑色描边的文字，保证在亮/暗画面上都清晰."""
    cv2.putText(img, text, org, _FONT, scale, (0, 0, 0), thick + 2,
                cv2.LINE_AA)
    cv2.putText(img, text, org, _FONT, scale, color, thick, cv2.LINE_AA)


def _overlay(img, app):
    st = app.streamer.state()
    cam = app.camera.state()
    h, w = img.shape[:2]

    live = st["state"] == "live"
    dot_color = (40, 40, 230) if live else (130, 130, 130)
    cv2.circle(img, (22, 24), 7, dot_color, -1, cv2.LINE_AA)
    _text(img, "LIVE" if live else "STANDBY", (36, 31), 0.55,
          (80, 80, 255) if live else (200, 200, 200), 1)

    _text(img, f"{cam['width']}x{cam['height']}  {cam['fps']:.1f} fps",
          (w - 215, 31), 0.5)

    if live:
        line = (f"PUSH {st['kbps']:.0f} kbps  {st['sent_fps']:.1f} fps  "
                f"{st['uptime_s']}s")
    elif st["state"] in ("connecting", "retrying"):
        line = f"PUSH {st['state']}..."
    elif st["state"] == "error":
        line = "PUSH ERROR"
    else:
        line = "PUSH idle (press S to start)"
    _text(img, line, (16, h - 38), 0.5,
          (80, 80, 255) if st["state"] == "error" else (220, 220, 220))

    if w < 700:
        _text(img, f"Web :{app.cfg['web']['port']}", (16, h - 14), 0.45,
              (180, 200, 220))
        _text(img, "S push  Q quit", (w - 150, h - 14), 0.42,
              (150, 150, 150))
    else:
        _text(img, f"Web: {app.urls[0]}", (16, h - 14), 0.45, (180, 200, 220))
        _text(img, "S: push/stop   R: web url   Q: quit",
              (w - 300, h - 14), 0.42, (150, 150, 150))
    return img


def run_preview(app, cfg, log=None):
    """返回 True 表示正常退出；False 表示窗口不可用（调用方应转无界面）."""
    log = log or (lambda msg: None)
    target_w = int(cfg.get("window_w", 960))
    target_h = int(target_w * 3 / 4)
    ui_fps = max(1, int(cfg.get("ui_fps", 15)))
    name = "Live Box - Local Preview"

    try:
        cv2.namedWindow(name, cv2.WINDOW_AUTOSIZE | cv2.WINDOW_GUI_NORMAL)
    except Exception as exc:                      # noqa: BLE001
        log(f"[ui] 无法创建预览窗口: {exc}")
        return False

    last_paint = 0.0
    try:
        while not app.stop_event.is_set():
            # 点击窗口右上角关闭按钮时，HighGUI 只销毁窗口，
            # 循环里的 imshow 又会把它建回来，所以需要主动检测
            try:
                if cv2.getWindowProperty(name, cv2.WND_PROP_VISIBLE) < 1:
                    app.shutdown()
                    break
            except cv2.error:
                break

            now = time.monotonic()
            if now - last_paint >= 1.0 / ui_fps:
                _, frame = app.camera.latest()
                if frame is not None:
                    h, w = frame.shape[:2]
                    scale = min(target_w / w, target_h / h)
                    view = cv2.resize(
                        frame, (max(1, int(w * scale)), max(1, int(h * scale))))
                    view = _overlay(view, app)
                    cv2.imshow(name, view)
                    last_paint = now

            key = cv2.waitKey(30) & 0xFF
            if key in (ord("q"), 27):
                app.shutdown()
                break
            if key == ord("s"):
                ok, msg = app.streamer.toggle()
                log(f"[ui] {msg}")
            elif key == ord("r"):
                log(f"[ui] Web 控制台: {app.urls[0]}")
    except Exception as exc:                      # noqa: BLE001
        log(f"[ui] 预览窗口异常: {exc}")
        return False
    finally:
        try:
            cv2.destroyWindow(name)
        except Exception:                          # noqa: BLE001
            pass
    return True
