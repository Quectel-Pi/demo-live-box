#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Web 控制台：直播配置 + MJPEG 本地预览.

只用标准库 http.server，不依赖 Flask / Django。
接口:
  GET  /                 配置页面
  GET  /api/status       运行状态 (JSON)
  GET  /api/config       当前配置 (JSON)
  POST /api/config       保存并应用配置
  POST /api/stream       {"action": "start" | "stop"}
  GET  /preview.mjpg     MJPEG 预览流
  GET  /snapshot.jpg     单帧快照
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>直播盒子 · Live Box</title>
<style>
* { box-sizing: border-box; }
body {
  margin: 0; background: #0f1115; color: #e8eaed;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans CJK SC",
               "Microsoft YaHei", sans-serif;
}
header {
  display: flex; align-items: center; justify-content: space-between;
  gap: 16px; padding: 18px 26px; background: #151821;
  border-bottom: 1px solid #232733; flex-wrap: wrap;
}
.brand { display: flex; align-items: center; gap: 12px; }
.logo {
  width: 38px; height: 38px; border-radius: 10px;
  background: linear-gradient(135deg, #3b82f6, #8b5cf6);
  box-shadow: 0 0 18px rgba(59,130,246,.45);
}
h1 { margin: 0; font-size: 19px; letter-spacing: .5px; }
h1 small { font-size: 11px; color: #8b93a5; margin-left: 6px; letter-spacing: 2px; }
header p { margin: 2px 0 0; font-size: 12px; color: #8b93a5; }
.pills { display: flex; gap: 8px; flex-wrap: wrap; }
.pill {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 6px 12px; border-radius: 20px; font-size: 12px;
  background: #1e2330; border: 1px solid #2c3242; color: #b6bdcb;
}
.pill::before { content: ""; width: 7px; height: 7px; border-radius: 50%;
  background: #6b7280; }
.pill.ok { border-color: rgba(34,197,94,.4); color: #86efac; }
.pill.ok::before { background: #22c55e; box-shadow: 0 0 8px #22c55e; }
.pill.warn { border-color: rgba(245,158,11,.4); color: #fcd34d; }
.pill.warn::before { background: #f59e0b; }
.pill.bad { border-color: rgba(239,68,68,.4); color: #fca5a5; }
.pill.bad::before { background: #ef4444; }
main {
  display: grid; gap: 16px; padding: 18px 26px 30px;
  grid-template-columns: minmax(420px, 1.5fr) minmax(340px, 1fr);
  align-items: start;
}
.panel {
  background: #151821; border: 1px solid #232733; border-radius: 12px;
  padding: 16px 18px;
}
.log-panel { grid-column: 1 / -1; }
.panel-head {
  display: flex; align-items: baseline; justify-content: space-between;
  gap: 10px; margin-bottom: 12px;
}
.panel-head h2 { margin: 0; font-size: 14px; letter-spacing: .5px; }
.hint { font-size: 12px; color: #8b93a5; }
.preview {
  position: relative; background: #000; border-radius: 10px;
  overflow: hidden; aspect-ratio: 16 / 9; border: 1px solid #232733;
}
.preview img { width: 100%; height: 100%; object-fit: contain; display: block; }
.badge-off, .badge-live {
  position: absolute; top: 10px; left: 10px; padding: 4px 10px;
  border-radius: 14px; font-size: 11px; font-weight: 700; letter-spacing: 1px;
  background: rgba(107,114,128,.85); color: #fff;
}
.badge-live { background: #dc2626; box-shadow: 0 0 12px rgba(220,38,38,.7); }
.nosignal {
  position: absolute; inset: 0; display: none; align-items: center;
  justify-content: center; font-size: 14px; letter-spacing: 2px; color: #6b7280;
  background: repeating-linear-gradient(45deg, #101216, #101216 10px,
              #14161b 10px, #14161b 20px);
}
.preview.empty .nosignal { display: flex; }
.preview.empty img { visibility: hidden; }
.metrics {
  display: grid; grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 8px; margin-top: 12px;
}
.metric {
  background: #1b1f2a; border: 1px solid #262b38; border-radius: 8px;
  padding: 8px 10px; min-width: 0;
}
.metric span { display: block; font-size: 11px; color: #8b93a5; }
.metric b {
  display: block; margin-top: 3px; font-size: 14px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.streamline {
  display: flex; gap: 10px; align-items: center; margin-top: 10px;
  font-size: 12px; color: #8b93a5;
}
.streamline code {
  color: #c9d1e0; background: #1b1f2a; border: 1px solid #262b38;
  padding: 4px 8px; border-radius: 6px; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; max-width: 100%;
}
.group { border-top: 1px solid #232733; padding-top: 12px; margin-top: 12px; }
.group:first-of-type { border-top: none; margin-top: 0; padding-top: 0; }
.group h3 {
  margin: 0 0 8px; font-size: 12px; color: #93a0b5;
  text-transform: uppercase; letter-spacing: 1px;
}
label { display: block; font-size: 12px; color: #9aa4b6; margin-bottom: 8px; }
label.check { display: flex; align-items: center; gap: 8px; }
input, select {
  width: 100%; margin-top: 4px; padding: 8px 10px; font-size: 13px;
  color: #e8eaed; background: #101319; border: 1px solid #2c3242;
  border-radius: 7px; outline: none;
}
input:focus, select:focus { border-color: #3b82f6; }
label.check input { width: auto; margin: 0; }
.row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.tip { margin: -2px 0 8px; font-size: 11px; color: #6f7a8d; line-height: 1.5; }
.proto { display: flex; align-items: center; gap: 8px; margin: -2px 0 10px; font-size: 11px; color: #6f7a8d; }
.proto b { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; color: #4c9aff; }
.actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 14px; }
button {
  padding: 9px 16px; border-radius: 8px; border: 1px solid #2c3242;
  background: #232936; color: #e8eaed; font-size: 13px; cursor: pointer;
}
button:hover { background: #2b3342; }
button.primary { background: #2563eb; border-color: #2563eb; font-weight: 600; }
button.primary:hover { background: #3b82f6; }
button.success { background: #15803d; border-color: #15803d; font-weight: 600; }
button.success:hover { background: #16a34a; }
button.danger { background: #b91c1c; border-color: #b91c1c; font-weight: 600; }
button.danger:hover { background: #dc2626; }
button.ghost { padding: 4px 10px; font-size: 12px; background: transparent; }
#logs {
  margin: 0; max-height: 210px; overflow: auto; font-size: 12px;
  line-height: 1.6; color: #a9b2c2; background: #101319;
  border: 1px solid #232733; border-radius: 8px; padding: 10px 12px;
  white-space: pre-wrap; word-break: break-all;
}
#toast {
  position: fixed; right: 24px; bottom: 24px; padding: 11px 16px;
  border-radius: 9px; font-size: 13px; background: #1f6f45; color: #fff;
  box-shadow: 0 8px 24px rgba(0,0,0,.45); opacity: 0;
  transform: translateY(8px); transition: all .2s; pointer-events: none;
}
#toast.show { opacity: 1; transform: translateY(0); }
#toast.err { background: #b91c1c; }
@media (max-width: 980px) {
  main { grid-template-columns: 1fr; padding: 14px; }
  .metrics { grid-template-columns: repeat(3, 1fr); }
  header { padding: 14px 16px; }
}
</style>
</head>
<body>
<header>
  <div class="brand">
    <div class="logo"></div>
    <div>
      <h1><span data-i18n="brand">直播盒子</span> <small>LIVE BOX</small></h1>
      <p data-i18n="tagline">USB 摄像头采集 · RTMP / SRT 推流 · Web 配置</p>
    </div>
  </div>
  <div class="pills">
    <span class="pill" id="pill-cam" data-i18n="camPill">摄像头</span>
    <span class="pill" id="pill-stream" data-i18n="streamPill">推流</span>
    <span class="pill" id="pill-web">Web</span>
    <button class="ghost" id="btn-lang" type="button" title="Language / 语言">EN</button>
  </div>
</header>

<main>
  <section class="panel">
    <div class="panel-head">
      <h2 data-i18n="preview">实时预览</h2>
      <div class="hint" id="preview-meta">--</div>
    </div>
    <div class="preview empty" id="preview-box">
      <img id="preview" alt="camera preview">
      <span class="badge-off" id="live-badge">OFFLINE</span>
      <span class="nosignal">NO SIGNAL</span>
    </div>
    <div class="metrics">
      <div class="metric"><span data-i18n="metricRes">分辨率</span><b id="m-res">--</b></div>
      <div class="metric"><span data-i18n="metricCamFps">采集帧率</span><b id="m-camfps">--</b></div>
      <div class="metric"><span data-i18n="metricPushFps">推流帧率</span><b id="m-pushfps">--</b></div>
      <div class="metric"><span data-i18n="metricKbps">推流码率</span><b id="m-kbps">--</b></div>
      <div class="metric"><span data-i18n="metricUptime">已推流</span><b id="m-uptime">--</b></div>
      <div class="metric"><span data-i18n="metricState">状态</span><b id="m-state">--</b></div>
    </div>
    <div class="streamline">
      <span data-i18n="streamUrl">推流地址</span><code id="m-url">未配置</code>
    </div>
  </section>

  <section class="panel">
    <div class="panel-head">
      <h2 data-i18n="configTitle">直播配置</h2>
      <div class="hint" data-i18n="autoApply">保存后自动应用</div>
    </div>

    <div class="group">
      <h3 data-i18n="captureSection">采集设置</h3>
      <label><span data-i18n="cameraDevice">摄像头设备</span>
        <input id="cam-device" placeholder="auto">
      </label>
      <div class="row">
        <label><span data-i18n="resolution">分辨率</span>
          <select id="cam-res">
            <option value="1280x720">1280x720</option>
            <option value="960x540">960x540</option>
            <option value="800x600">800x600</option>
            <option value="640x480">640x480</option>
          </select>
        </label>
        <label><span data-i18n="frameRate">帧率</span>
          <input id="cam-fps" type="number" min="5" max="30" step="1">
        </label>
      </div>
      <label><span data-i18n="pixelFormat">像素格式</span>
        <select id="cam-fourcc">
          <option value="MJPG">MJPG</option>
          <option value="YUYV">YUYV</option>
        </select>
      </label>
      <label><span data-i18n="rotation">画面旋转</span>
        <select id="cam-rotate">
          <option value="0" data-i18n="rot0">0°（横装）</option>
          <option value="90" data-i18n="rot90">顺时针 90°</option>
          <option value="180" data-i18n="rot180">180°（倒装）</option>
          <option value="270" data-i18n="rot270">逆时针 90°</option>
        </select>
      </label>
    </div>

    <div class="group">
      <h3 data-i18n="streamSection">推流设置</h3>
      <label><span data-i18n="streamUrl">推流地址</span>
        <input id="st-url" placeholder="rtmp://live-push.example.com/live/your_key">
      </label>
      <p class="tip" data-i18n="streamTip">更换协议只需改地址前缀：rtmp://、rtmps://、srt://，地址请从直播平台后台获取</p>
      <div class="proto"><span data-i18n="protocol">协议</span><b id="st-proto">--</b><span data-i18n="protoAuto">由地址前缀自动识别</span></div>
      <div class="row">
        <label><span data-i18n="encoder">编码器</span>
          <select id="st-encoder">
            <option value="libx264" data-i18n="encSoft">libx264（软编）</option>
            <option value="mpph264enc" data-i18n="encHard">mpph264enc（RK MPP 硬编）</option>
          </select>
        </label>
        <label><span data-i18n="bitrate">码率 (kbps)</span>
          <input id="st-bitrate" type="number" min="300" max="12000" step="100">
        </label>
      </div>
      <div class="row">
        <label><span data-i18n="keyframe">关键帧间隔 (s)</span>
          <input id="st-gop" type="number" min="1" max="10">
        </label>
        <label class="check" style="margin-top:24px">
          <input id="st-auto" type="checkbox"> <span data-i18n="autoReconnect">断线自动重连</span>
        </label>
      </div>
    </div>

    <div class="group">
      <h3 data-i18n="audioSection">音频</h3>
      <label class="check"><input id="au-enabled" type="checkbox"> <span data-i18n="includeAudio">推流附带音频</span></label>
      <label><span data-i18n="audioDevice">音频设备</span>
        <input id="au-device" placeholder="plughw:0,0">
      </label>
      <p class="tip" data-i18n="audioTip">arecord -l 查看设备；板载声卡常见为 plughw:0,0</p>
    </div>

    <div class="group">
      <h3 data-i18n="webPreview">Web 预览</h3>
      <div class="row">
        <label><span data-i18n="previewFps">预览帧率</span>
          <input id="web-fps" type="number" min="1" max="30">
        </label>
        <label><span data-i18n="quality">画质 (30-95)</span>
          <input id="web-quality" type="number" min="30" max="95">
        </label>
      </div>
    </div>

    <div class="actions">
      <button class="primary" id="btn-save" data-i18n="save">保存并应用</button>
      <button class="success" id="btn-start" data-i18n="start">开始推流</button>
      <button class="danger" id="btn-stop" data-i18n="stop">停止推流</button>
    </div>
  </section>

  <section class="panel log-panel">
    <div class="panel-head">
      <h2 data-i18n="logsTitle">运行日志</h2>
      <button class="ghost" id="btn-refresh" data-i18n="refresh">刷新</button>
    </div>
    <pre id="logs" data-i18n="loading">加载中…</pre>
  </section>
</main>

<div id="toast"></div>

<script>
const $ = (id) => document.getElementById(id);
let lastCamOpened = false;
let configLoaded = false;
let lastStatus = null;

const I18N = {
  zh: {
    docTitle: '直播盒子 · Live Box',
    brand: '直播盒子',
    tagline: 'USB 摄像头采集 · RTMP / SRT 推流 · Web 配置',
    camPill: '摄像头', streamPill: '推流',
    preview: '实时预览', configTitle: '直播配置', autoApply: '保存后自动应用',
    metricRes: '分辨率', metricCamFps: '采集帧率', metricPushFps: '推流帧率',
    metricKbps: '推流码率', metricUptime: '已推流', metricState: '状态',
    streamUrl: '推流地址', notConfigured: '未配置',
    captureSection: '采集设置', cameraDevice: '摄像头设备', resolution: '分辨率',
    frameRate: '帧率', pixelFormat: '像素格式', rotation: '画面旋转',
    rot0: '0°（横装）', rot90: '顺时针 90°', rot180: '180°（倒装）', rot270: '逆时针 90°',
    streamSection: '推流设置', encoder: '编码器',
    encSoft: 'libx264（软编）', encHard: 'mpph264enc（RK MPP 硬编）',
    bitrate: '码率 (kbps)', keyframe: '关键帧间隔 (s)', autoReconnect: '断线自动重连',
    streamTip: '更换协议只需改地址前缀：rtmp://、rtmps://、srt://，地址请从直播平台后台获取',
    protocol: '协议', protoAuto: '由地址前缀自动识别',
    audioSection: '音频', includeAudio: '推流附带音频', audioDevice: '音频设备',
    audioTip: 'arecord -l 查看设备；板载声卡常见为 plughw:0,0',
    webPreview: 'Web 预览', previewFps: '预览帧率', quality: '画质 (30-95)',
    save: '保存并应用', start: '开始推流', stop: '停止推流',
    logsTitle: '运行日志', refresh: '刷新', loading: '加载中…', noLogs: '暂无日志',
    camOnline: '摄像头 {w}x{h}', camOffline: '摄像头离线', waitingCamera: '等待摄像头',
    webDisconnected: 'Web 连接中断', parseError: '响应解析失败',
    saved: '配置已保存', started: '开始推流', stopped: '已停止推流',
    stateIdle: '未推流', stateConnecting: '连接中', stateLive: '推流中',
    stateRetrying: '重连中', stateError: '推流异常', stateUnknown: '未知'
  },
  en: {
    docTitle: 'Live Box · Web Console',
    brand: 'Live Box',
    tagline: 'USB camera capture · RTMP / SRT streaming · Web configuration',
    camPill: 'Camera', streamPill: 'Stream',
    preview: 'Live Preview', configTitle: 'Stream Settings', autoApply: 'Applied automatically after saving',
    metricRes: 'Resolution', metricCamFps: 'Capture FPS', metricPushFps: 'Stream FPS',
    metricKbps: 'Bitrate', metricUptime: 'Uptime', metricState: 'Status',
    streamUrl: 'Stream URL', notConfigured: 'Not configured',
    captureSection: 'Capture', cameraDevice: 'Camera device', resolution: 'Resolution',
    frameRate: 'Frame rate', pixelFormat: 'Pixel format', rotation: 'Rotation',
    rot0: '0° (landscape)', rot90: '90° clockwise', rot180: '180° (upside down)', rot270: '90° counter-clockwise',
    streamSection: 'Streaming', encoder: 'Encoder',
    encSoft: 'libx264 (software)', encHard: 'mpph264enc (RK MPP hardware)',
    bitrate: 'Bitrate (kbps)', keyframe: 'Keyframe interval (s)', autoReconnect: 'Auto reconnect',
    streamTip: 'To switch protocols, change the URL prefix: rtmp://, rtmps://, srt:// — get the URL from your platform console',
    protocol: 'Protocol', protoAuto: 'detected from the URL prefix',
    audioSection: 'Audio', includeAudio: 'Include audio in stream', audioDevice: 'Audio device',
    audioTip: 'Run arecord -l to list devices; the onboard sound card is usually plughw:0,0',
    webPreview: 'Web Preview', previewFps: 'Preview FPS', quality: 'Quality (30-95)',
    save: 'Save and apply', start: 'Start streaming', stop: 'Stop streaming',
    logsTitle: 'Logs', refresh: 'Refresh', loading: 'Loading…', noLogs: 'No logs yet',
    camOnline: 'Camera {w}x{h}', camOffline: 'Camera offline', waitingCamera: 'Waiting for camera',
    webDisconnected: 'Web disconnected', parseError: 'Failed to parse response',
    saved: 'Configuration saved', started: 'Streaming started', stopped: 'Streaming stopped',
    stateIdle: 'Idle', stateConnecting: 'Connecting', stateLive: 'Live',
    stateRetrying: 'Reconnecting', stateError: 'Error', stateUnknown: 'Unknown'
  }
};

const STATE_KEYS = {
  idle: 'stateIdle', connecting: 'stateConnecting', live: 'stateLive',
  retrying: 'stateRetrying', error: 'stateError'
};

let lang = new URLSearchParams(location.search).get('lang')
  || localStorage.getItem('livebox-lang')
  || ((navigator.language || 'zh').toLowerCase().startsWith('zh') ? 'zh' : 'en');
if (!I18N[lang]) lang = 'zh';

function t(key, vars) {
  let s = (I18N[lang] && I18N[lang][key]) || I18N.zh[key] || key;
  if (vars) {
    Object.keys(vars).forEach((k) => {
      s = s.split('{' + k + '}').join(String(vars[k]));
    });
  }
  return s;
}

function stateLabel(state) {
  return STATE_KEYS[state] ? t(STATE_KEYS[state]) : (state || t('stateUnknown'));
}

function updateProto() {
  const v = ($('st-url').value || '').trim().toLowerCase();
  const p = v.startsWith('rtmps://') ? 'RTMPS'
    : v.startsWith('rtmp://') ? 'RTMP'
    : v.startsWith('srt://') ? 'SRT' : '--';
  $('st-proto').textContent = p;
}

$('st-url').addEventListener('input', updateProto);

function applyLang() {
  document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en';
  document.title = t('docTitle');
  document.querySelectorAll('[data-i18n]').forEach((el) => {
    el.textContent = t(el.dataset.i18n);
  });
  $('btn-lang').textContent = lang === 'zh' ? 'EN' : '中文';
  if (lastStatus) render(lastStatus);
  else {
    $('m-url').textContent = t('notConfigured');
  }
}

$('btn-lang').onclick = () => {
  lang = lang === 'zh' ? 'en' : 'zh';
  localStorage.setItem('livebox-lang', lang);
  applyLang();
};

function toast(msg, ok = true) {
  const el = $('toast');
  el.textContent = msg;
  el.className = ok ? 'show' : 'show err';
  clearTimeout(el._timer);
  el._timer = setTimeout(() => { el.className = ''; }, 2600);
}

async function api(path, options) {
  const res = await fetch(path, options);
  const data = await res.json().catch(() => ({ ok: false, message: t('parseError') }));
  if (!res.ok || data.ok === false) {
    throw new Error(data.message || ('HTTP ' + res.status));
  }
  return data;
}

function setForm(cfg) {
  const cam = cfg.camera || {};
  const st = cfg.stream || {};
  const au = st.audio || {};
  const web = cfg.web || {};
  $('cam-device').value = cam.device || 'auto';
  const res = (cam.width || 1280) + 'x' + (cam.height || 720);
  $('cam-res').value = res;
  if (!$('cam-res').value) $('cam-res').value = '1280x720';
  $('cam-fps').value = cam.fps || 15;
  $('cam-fourcc').value = (cam.fourcc || 'MJPG').toUpperCase();
  $('cam-rotate').value = String(cam.rotate || 0);
  $('st-url').value = st.url || '';
  updateProto();
  $('st-encoder').value = st.encoder || 'libx264';
  $('st-bitrate').value = st.bitrate_kbps || 2500;
  $('st-gop').value = st.gop_seconds || 2;
  $('st-auto').checked = st.auto_reconnect !== false;
  $('au-enabled').checked = !!au.enabled;
  $('au-device').value = au.device || 'plughw:0,0';
  $('web-fps').value = web.preview_fps || 10;
  $('web-quality').value = web.preview_quality || 70;
  configLoaded = true;
}

function collectForm() {
  const res = ($('cam-res').value || '1280x720').split('x');
  return {
    camera: {
      device: $('cam-device').value.trim() || 'auto',
      width: parseInt(res[0], 10) || 1280,
      height: parseInt(res[1], 10) || 720,
      fps: parseInt($('cam-fps').value, 10) || 15,
      fourcc: $('cam-fourcc').value,
      rotate: parseInt($('cam-rotate').value, 10) || 0
    },
    stream: {
      url: $('st-url').value.trim(),
      encoder: $('st-encoder').value,
      bitrate_kbps: parseInt($('st-bitrate').value, 10) || 2500,
      gop_seconds: parseInt($('st-gop').value, 10) || 2,
      auto_reconnect: $('st-auto').checked,
      audio: {
        enabled: $('au-enabled').checked,
        device: $('au-device').value.trim() || 'plughw:0,0'
      }
    },
    web: {
      preview_fps: parseInt($('web-fps').value, 10) || 10,
      preview_quality: parseInt($('web-quality').value, 10) || 70
    }
  };
}

function fmtDur(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  const pad = (n) => String(n).padStart(2, '0');
  return h > 0 ? (h + ':' + pad(m) + ':' + pad(s)) : (pad(m) + ':' + pad(s));
}

function setPill(id, text, cls) {
  const el = $(id);
  el.textContent = text;
  el.className = 'pill ' + (cls || '');
}

function render(s) {
  lastStatus = s;
  const cam = s.camera || {};
  const st = s.stream || {};
  const web = s.web || {};

  setPill('pill-cam', cam.opened ? t('camOnline', { w: cam.width, h: cam.height }) : t('camOffline'),
          cam.opened ? 'ok' : 'bad');
  setPill('pill-stream', stateLabel(st.state),
          st.state === 'live' ? 'ok' : (st.state === 'error' ? 'bad' : 'warn'));
  setPill('pill-web', 'Web :' + web.port, 'ok');

  $('preview-meta').textContent = cam.opened
    ? (cam.width + 'x' + cam.height + ' · ' + (cam.fps || 0).toFixed(1) + ' fps')
    : (cam.error || t('waitingCamera'));
  $('m-res').textContent = cam.opened ? (cam.width + 'x' + cam.height) : '--';
  $('m-camfps').textContent = cam.opened ? (cam.fps || 0).toFixed(1) : '--';
  $('m-pushfps').textContent = st.active ? (st.sent_fps || 0).toFixed(1) : '--';
  $('m-kbps').textContent = st.active ? (Math.round(st.kbps || 0) + ' kbps') : '--';
  $('m-uptime').textContent = st.active ? fmtDur(st.uptime_s) : '--';
  $('m-state').textContent = st.state ? stateLabel(st.state) : '--';
  $('m-url').textContent = st.url || t('notConfigured');

  const box = $('preview-box');
  if (cam.opened) box.classList.remove('empty');
  else box.classList.add('empty');
  const badge = $('live-badge');
  if (st.state === 'live') {
    badge.textContent = 'LIVE';
    badge.className = 'badge-live';
  } else {
    badge.textContent = 'OFFLINE';
    badge.className = 'badge-off';
  }
  if (cam.opened && !lastCamOpened) {
    $('preview').src = '/preview.mjpg?t=' + Date.now();
  }
  lastCamOpened = !!cam.opened;

  const logs = (s.logs || []).join(String.fromCharCode(10));
  const el = $('logs');
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  el.textContent = logs || t('noLogs');
  if (atBottom) el.scrollTop = el.scrollHeight;
}

async function poll() {
  try {
    render(await api('/api/status'));
  } catch (e) {
    setPill('pill-web', t('webDisconnected'), 'bad');
  }
}

async function loadConfig() {
  try {
    const d = await api('/api/config');
    setForm(d.config);
  } catch (e) {
    toast(e.message, false);
  }
}

$('btn-save').onclick = async () => {
  try {
    const d = await api('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(collectForm())
    });
    if (d.config) setForm(d.config);
    toast(t('saved'));
    poll();
  } catch (e) {
    toast(e.message, false);
  }
};

$('btn-start').onclick = async () => {
  try {
    const d = await api('/api/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'start' })
    });
    toast(t('started'));
    poll();
  } catch (e) {
    toast(e.message, false);
  }
};

$('btn-stop').onclick = async () => {
  try {
    const d = await api('/api/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'stop' })
    });
    toast(t('stopped'));
    poll();
  } catch (e) {
    toast(e.message, false);
  }
};

$('btn-refresh').onclick = poll;

applyLang();
loadConfig();
poll();
setInterval(poll, 1500);
</script>
</body>
</html>
"""


def _make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "LiveBox/1.0"

        def log_message(self, fmt, *args):        # noqa: A003
            pass

        # ---------------- 响应工具 ----------------
        def _send_bytes(self, body, content_type, code=200):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _send_json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self._send_bytes(body, "application/json; charset=utf-8", code)

        def _read_json(self):
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise ValueError("Invalid Content-Length")
            if length <= 0:
                raise ValueError("Empty request body")
            if length > 262144:
                raise ValueError("Request body too large")
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ValueError("Invalid JSON")

        # ---------------- GET ----------------
        def do_GET(self):                          # noqa: N802
            path = urlsplit(self.path).path
            if path in ("/", "/index.html"):
                self._send_bytes(HTML.encode("utf-8"),
                                 "text/html; charset=utf-8")
            elif path == "/api/status":
                self._send_json(app.status())
            elif path == "/api/config":
                self._send_json({"ok": True, "config": app.cfg})
            elif path == "/preview.mjpg":
                self._send_mjpeg()
            elif path == "/snapshot.jpg":
                jpg = app.camera.snapshot_jpeg(
                    int(app.cfg["web"].get("preview_quality", 70)))
                if jpg is None:
                    self._send_json({"ok": False, "message": "No image available"}, 503)
                else:
                    self._send_bytes(jpg, "image/jpeg")
            elif path == "/favicon.ico":
                self._send_bytes(b"", "image/x-icon", 204)
            else:
                self._send_json({"ok": False, "message": "not found"}, 404)

        # ---------------- POST ----------------
        def do_POST(self):                         # noqa: N802
            path = urlsplit(self.path).path
            try:
                data = self._read_json()
                if path == "/api/config":
                    ok, msg, cfg = app.update_config(data)
                    self._send_json({"ok": ok, "message": msg, "config": cfg},
                                    200 if ok else 400)
                elif path == "/api/stream":
                    action = str(data.get("action", "")).lower()
                    if action == "start":
                        ok, msg = app.start_stream()
                    elif action == "stop":
                        ok, msg = app.stop_stream()
                    else:
                        raise ValueError("action must be start or stop")
                    self._send_json({"ok": ok, "message": msg},
                                    200 if ok else 400)
                else:
                    self._send_json({"ok": False, "message": "not found"}, 404)
            except ValueError as exc:
                self._send_json({"ok": False, "message": str(exc)}, 400)
            except Exception as exc:               # noqa: BLE001
                app.log(f"[web] Request handler error: {exc}")
                self._send_json({"ok": False, "message": "Internal server error"}, 500)

        # ---------------- MJPEG ----------------
        def _send_mjpeg(self):
            self.send_response(200)
            self.send_header("Content-Type",
                             "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-cache, no-store, "
                                              "must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            try:
                while not app.stop_event.is_set():
                    web_cfg = app.cfg["web"]
                    quality = int(web_cfg.get("preview_quality", 70))
                    fps = max(1, int(web_cfg.get("preview_fps", 10)))
                    jpg = app.camera.snapshot_jpeg(quality)
                    if jpg is None:
                        time.sleep(0.2)
                        continue
                    self.wfile.write(b"--frame\r\n")
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(
                        f"Content-Length: {len(jpg)}\r\n\r\n".encode())
                    self.wfile.write(jpg)
                    self.wfile.write(b"\r\n")
                    self.wfile.flush()
                    if app.stop_event.wait(1.0 / fps):
                        break
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    return Handler


class WebServer:
    def __init__(self, app, cfg, log=None):
        self.app = app
        self._log = log or (lambda msg: None)
        self.host = str(cfg.get("host", "0.0.0.0"))
        self.port = int(cfg.get("port", 8080))
        self.httpd = None
        self._thread = None

    def start(self):
        try:
            self.httpd = ThreadingHTTPServer((self.host, self.port),
                                             _make_handler(self.app))
        except OSError as exc:
            self._log(f"[web] Failed to start on port {self.port}: {exc}")
            return False
        self.httpd.daemon_threads = True
        self._thread = threading.Thread(target=self.httpd.serve_forever,
                                        name="web", daemon=True)
        self._thread.start()
        self._log(f"[web] Console started: http://{self.host}:{self.port}")
        return True

    def stop(self):
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
