document.addEventListener('DOMContentLoaded', () => {
  const grid = document.getElementById('camGrid');
  const emptyMsg = document.getElementById('emptyMsg');
  const pickBtn = document.getElementById('pickBtn');
  const pickPanel = document.getElementById('pickPanel');
  const pickList = document.getElementById('pickList');
  const pickAllBtn = document.getElementById('pickAllBtn');
  const pickNoneBtn = document.getElementById('pickNoneBtn');
  const closePickBtn = document.getElementById('closePickBtn');
  const fsAllBtn = document.getElementById('fsAllBtn');

  const STORAGE_KEY = 'seccam_multicam_selected';
  const HLS_CONFIG = {
    lowLatencyMode: true,
    backBufferLength: 30,
    maxBufferLength: 10,
    maxMaxBufferLength: 30,
    liveSyncDurationCount: 2,
    manifestLoadingMaxRetry: 2,
    manifestLoadingRetryDelay: 500,
    manifestLoadingMaxRetryTimeout: 4000,
    levelLoadingMaxRetry: 4,
    fragLoadingMaxRetry: 6,
  };
  const RETRY_DELAY_MS = 4000;

  let allCams = [];
  let selected = null; // array de nomes na ordem exibida
  const tiles = new Map(); // name -> <figure>
  const players = new Map(); // name -> { video, hls, retry, retryTimer }
  let dragName = null;
  let fsActive = false; // true enquanto QUALQUER elemento esta em tela cheia

  // ── Seleção persistida ────────────────────────────────────────────────────
  function loadSelection() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      const arr = JSON.parse(raw);
      return Array.isArray(arr) ? arr : null;
    } catch (_) { return null; }
  }
  function saveSelection() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(selected)); } catch (_) {}
  }

  // ── Layout: quantas câmeras por linha para preencher a tela ───────────────
  function computeRows(n, W, H) {
    if (n <= 0) return [];
    if (n === 1) return [1];
    const target = 16 / 9;
    let best = null;
    for (let c = 1; c <= n; c++) {
      const r = Math.ceil(n / c);
      const aspect = (W / c) / (H / r);
      const score = Math.abs(Math.log(aspect / target)) + (c * r - n) * 0.2 - 0.001 * c;
      if (!best || score < best.score) best = { r, score };
    }
    // Distribui as câmeras o mais uniforme possivel entre as linhas.
    const rows = [];
    let remaining = n;
    for (let i = 0; i < best.r; i++) {
      const count = Math.ceil(remaining / (best.r - i));
      rows.push(count);
      remaining -= count;
    }
    return rows;
  }

  function renderGrid() {
    if (!selected) return;
    // Nunca remonta a grade durante o fullscreen: mover o elemento no DOM
    // faria o navegador sair da tela cheia.
    if (fsActive || document.fullscreenElement || document.webkitFullscreenElement) return;
    const valid = new Set(allCams.map((c) => c.name));
    selected = selected.filter((n) => valid.has(n));

    emptyMsg.classList.toggle('hidden', selected.length > 0);
    grid.innerHTML = '';

    // Desmonta streams de cameras que sairam do layout (economiza banda).
    for (const name of Array.from(tiles.keys())) {
      if (!selected.includes(name)) {
        destroyPlayer(name);
        const t = tiles.get(name);
        if (t) t.remove();
        tiles.delete(name);
      }
    }

    if (selected.length === 0) return;

    const rows = computeRows(selected.length, window.innerWidth, window.innerHeight);
    let idx = 0;
    for (const count of rows) {
      const row = document.createElement('div');
      row.className = 'mc-row';
      for (let i = 0; i < count && idx < selected.length; i++, idx++) {
        row.appendChild(getTile(selected[idx]));
      }
      grid.appendChild(row);
    }
  }

  function getTile(name) {
    if (tiles.has(name)) return tiles.get(name);
    const cam = allCams.find((c) => c.name === name);
    const tile = buildTile(cam);
    tiles.set(name, tile);
    startStream(name);
    return tile;
  }

  // ── Tile ──────────────────────────────────────────────────────────────────
  function buildTile(cam) {
    const tile = document.createElement('figure');
    tile.className = 'mc-tile';
    tile.dataset.name = cam.name;
    tile.draggable = true;

    const video = document.createElement('video');
    video.className = 'mc-video';
    video.muted = true;
    video.autoplay = true;
    video.playsInline = true;
    video.setAttribute('playsinline', '');
    video.setAttribute('muted', '');
    video.setAttribute('draggable', 'false');

    const status = document.createElement('span');
    status.className = 'mc-status';

    const audioBtn = document.createElement('button');
    audioBtn.className = 'mc-btn mc-audio-btn';
    audioBtn.title = 'Ativar som';
    audioBtn.textContent = '🔇';
    audioBtn.addEventListener('click', (e) => { e.stopPropagation(); toggleAudio(cam.name); });

    const fsBtn = document.createElement('button');
    fsBtn.className = 'mc-btn';
    fsBtn.title = 'Tela cheia';
    fsBtn.textContent = '⛶';
    fsBtn.addEventListener('click', (e) => { e.stopPropagation(); toggleFullscreen(tile); });

    tile.append(video, status, audioBtn, fsBtn);

    const player = { video, hls: null, pc: null, whepLocation: null, usingWebrtc: false, retry: 0, retryTimer: null };
    players.set(cam.name, player);

    const zoom = enableZoom(tile, video);

    video.addEventListener('playing', () => { player.retry = 0; });
    video.addEventListener('error', () => { if (!player.hls && !player.pc) scheduleRetry(cam.name, 'erro'); });

    // Duplo toque/clique: se ampliado, volta ao normal; senão, tela cheia.
    tile.addEventListener('dblclick', () => {
      if (zoom.isZoomed()) zoom.reset();
      else toggleFullscreen(tile);
    });

    // ── Drag and drop ──
    tile.addEventListener('dragstart', (e) => {
      dragName = cam.name;
      tile.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
      try { e.dataTransfer.setData('text/plain', cam.name); } catch (_) {}
    });
    tile.addEventListener('dragend', () => {
      dragName = null;
      clearDropMarks();
      tile.classList.remove('dragging');
    });
    tile.addEventListener('dragover', (e) => {
      if (!dragName || dragName === cam.name) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      const rect = tile.getBoundingClientRect();
      const right = (e.clientX - rect.left) > rect.width / 2;
      tile.classList.toggle('drop-right', right);
      tile.classList.toggle('drop-left', !right);
    });
    tile.addEventListener('dragleave', () => tile.classList.remove('drop-left', 'drop-right'));
    tile.addEventListener('drop', (e) => {
      if (!dragName || dragName === cam.name) return;
      e.preventDefault();
      const rect = tile.getBoundingClientRect();
      const right = (e.clientX - rect.left) > rect.width / 2;
      reorder(dragName, cam.name, right ? 'after' : 'before');
    });

    return tile;
  }

  // ── Zoom por câmera (pinça no toque / roda no desktop) ────────────────────
  function enableZoom(tile, video) {
    const MIN = 1, MAX = 8;
    let scale = 1, tx = 0, ty = 0;
    const pointers = new Map();
    let pinch = null;
    let panStart = null;

    function apply() {
      video.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`;
      tile.classList.toggle('is-zoomed', scale > 1.01);
      // Enquanto ampliado, o arrastar serve para navegar (não para reordenar).
      tile.draggable = scale <= 1.01;
    }

    function clampTranslate() {
      const r = tile.getBoundingClientRect();
      const maxX = (scale - 1) * r.width / 2;
      const maxY = (scale - 1) * r.height / 2;
      tx = Math.max(-maxX, Math.min(maxX, tx));
      ty = Math.max(-maxY, Math.min(maxY, ty));
    }

    function reset() {
      scale = 1; tx = 0; ty = 0;
      pinch = null; panStart = null; pointers.clear();
      apply();
    }

    function rel(pt) {
      const r = tile.getBoundingClientRect();
      return { x: pt.clientX - r.left - r.width / 2, y: pt.clientY - r.top - r.height / 2 };
    }
    const dist = (a, b) => Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
    const mid = (a, b) => ({ clientX: (a.clientX + b.clientX) / 2, clientY: (a.clientY + b.clientY) / 2 });

    tile.addEventListener('pointerdown', (e) => {
      if (e.target.closest('.mc-btn')) return;
      // Com escala 1 e mouse, deixa o drag-and-drop nativo agir.
      if (e.pointerType === 'mouse' && scale <= 1.01) return;
      pointers.set(e.pointerId, e);
      try { tile.setPointerCapture(e.pointerId); } catch (_) {}

      if (pointers.size === 2) {
        const [a, b] = [...pointers.values()];
        pinch = { d: dist(a, b), scale, m: rel(mid(a, b)), tx, ty };
        panStart = null;
      } else if (pointers.size === 1 && scale > 1.01) {
        panStart = { x: e.clientX, y: e.clientY, tx, ty };
      }
    });

    tile.addEventListener('pointermove', (e) => {
      if (!pointers.has(e.pointerId)) return;
      pointers.set(e.pointerId, e);

      if (pointers.size >= 2 && pinch) {
        const [a, b] = [...pointers.values()];
        const ns = Math.max(MIN, Math.min(MAX, pinch.scale * (dist(a, b) / pinch.d)));
        const k = ns / pinch.scale;
        const m = rel(mid(a, b));
        tx = m.x - (pinch.m.x - pinch.tx) * k;
        ty = m.y - (pinch.m.y - pinch.ty) * k;
        scale = ns;
        clampTranslate();
        apply();
        e.preventDefault();
      } else if (pointers.size === 1 && panStart && scale > 1.01) {
        tx = panStart.tx + (e.clientX - panStart.x);
        ty = panStart.ty + (e.clientY - panStart.y);
        clampTranslate();
        apply();
        e.preventDefault();
      }
    });

    function endPointer(e) {
      if (!pointers.has(e.pointerId)) return;
      pointers.delete(e.pointerId);
      try { tile.releasePointerCapture(e.pointerId); } catch (_) {}
      if (pointers.size < 2) pinch = null;
      if (pointers.size === 1 && scale > 1.01) {
        const p = [...pointers.values()][0];
        panStart = { x: p.clientX, y: p.clientY, tx, ty };
      } else if (pointers.size === 0) {
        panStart = null;
      }
      if (scale <= 1.01) reset();
    }
    tile.addEventListener('pointerup', endPointer);
    tile.addEventListener('pointercancel', endPointer);

    // Roda do mouse: zoom no ponto do cursor.
    tile.addEventListener('wheel', (e) => {
      e.preventDefault();
      const ns = Math.max(MIN, Math.min(MAX, scale * (e.deltaY < 0 ? 1.12 : 1 / 1.12)));
      const k = ns / scale;
      const m = rel(e);
      tx = m.x - (m.x - tx) * k;
      ty = m.y - (m.y - ty) * k;
      scale = ns;
      if (scale <= 1.01) reset();
      else { clampTranslate(); apply(); }
    }, { passive: false });

    apply();
    return { isZoomed: () => scale > 1.01, reset };
  }

  function setStatus(tile, text, cls) {
    const el = tile.querySelector('.mc-status');
    if (!el) return;
    el.textContent = text;
    el.className = 'mc-status' + (cls ? ' ' + cls : '');
  }

  // ── Áudio ─────────────────────────────────────────────────────────────────
  // Ativa o som de UMA câmera (modo solo) e silencia as demais. O clique do
  // usuário é o gesto exigido pelo navegador para liberar áudio no autoplay.
  function updateAudioBtn(tile, muted) {
    const btn = tile && tile.querySelector('.mc-audio-btn');
    if (!btn) return;
    btn.textContent = muted ? '🔇' : '🔊';
    btn.title = muted ? 'Ativar som' : 'Silenciar';
  }

  function toggleAudio(name) {
    const player = players.get(name);
    if (!player) return;
    const v = player.video;
    const turnOn = v.muted; // estava mudo -> agora liga

    if (turnOn) {
      players.forEach((p, n) => {
        if (n !== name && !p.video.muted) {
          p.video.muted = true;
          updateAudioBtn(tiles.get(n), true);
        }
      });
      v.muted = false;
      v.volume = 1;
    } else {
      v.muted = true;
    }

    updateAudioBtn(tiles.get(name), v.muted);
    if (turnOn) v.play().catch(() => {});
  }

  // ── Reordenar com drag ────────────────────────────────────────────────────
  function clearDropMarks() {
    tiles.forEach((t) => t.classList.remove('drop-left', 'drop-right'));
  }

  function reorder(fromName, targetName, pos) {
    const from = selected.indexOf(fromName);
    if (from < 0) return;
    let to = selected.indexOf(targetName);
    if (to < 0) return;
    if (pos === 'after') to += 1;
    if (from < to) to -= 1;
    const [moved] = selected.splice(from, 1);
    selected.splice(to, 0, moved);
    saveSelection();
    renderGrid();
  }

  // ── Streams: WebRTC (WHEP) por padrão, HLS como fallback ─────────────────
  function startStream(name) {
    const cam = allCams.find((c) => c.name === name);
    const player = players.get(name);
    if (!cam || !player) return;
    destroyStreams(player);
    setStatus(tiles.get(name), '');

    if (cam.webrtc_url && window.RTCPeerConnection) {
      startWhep(name, cam, player);
    } else {
      startHls(name, cam, player);
    }
  }

  function startWhep(name, cam, player) {
    let pc;
    try {
      pc = new RTCPeerConnection({ iceServers: [] });
    } catch (_) {
      startHls(name, cam, player);
      return;
    }
    player.pc = pc;
    player.usingWebrtc = true;

    pc.addTransceiver('video', { direction: 'recvonly' });
    pc.addTransceiver('audio', { direction: 'recvonly' });

    pc.ontrack = (ev) => {
      if (ev.streams && ev.streams[0]) {
        player.video.srcObject = ev.streams[0];
      } else {
        const stream = player.video.srcObject || new MediaStream();
        stream.addTrack(ev.track);
        player.video.srcObject = stream;
      }
      player.video.play().catch(() => {});
    };

    pc.addEventListener('connectionstatechange', () => {
      if (player.pc !== pc) return; // já substituído/limpo
      if (pc.connectionState === 'failed') {
        scheduleRetry(name, 'reconectando');
      }
    });

    (async () => {
      try {
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);
        await waitIceGathering(pc, 2000);
        const res = await fetch(cam.webrtc_url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/sdp' },
          body: pc.localDescription.sdp,
        });
        if (!res.ok) throw new Error('WHEP ' + res.status);
        const loc = res.headers.get('Location');
        if (loc) player.whepLocation = new URL(loc, cam.webrtc_url).toString();
        const answer = await res.text();
        await pc.setRemoteDescription({ type: 'answer', sdp: answer });
        player.video.play().catch(() => {});
      } catch (_) {
        // WebRTC falhou: cai para HLS.
        if (player.pc === pc) {
          destroyWhep(player);
          startHls(name, cam, player);
        }
      }
    })();
  }

  function startHls(name, cam, player) {
    destroyWhep(player);
    player.usingWebrtc = false;
    if (!cam.hls_url) { setStatus(tiles.get(name), 'sem stream', 'is-error'); return; }

    if (window.Hls && window.Hls.isSupported()) {
      const hls = new window.Hls(HLS_CONFIG);
      player.hls = hls;
      hls.on(window.Hls.Events.ERROR, (_evt, data) => {
        if (!data.fatal) return;
        if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR) { hls.recoverMediaError(); return; }
        try { hls.destroy(); } catch (_) {}
        player.hls = null;
        scheduleRetry(name, data.type === window.Hls.ErrorTypes.NETWORK_ERROR ? 'sem rede' : 'sem sinal');
      });
      hls.attachMedia(player.video);
      hls.on(window.Hls.Events.MEDIA_ATTACHED, () => hls.loadSource(cam.hls_url));
    } else if (player.video.canPlayType('application/vnd.apple.mpegurl')) {
      player.video.src = cam.hls_url;
    } else {
      setStatus(tiles.get(name), 'vídeo não suportado', 'is-error');
      return;
    }
    player.video.play().catch(() => {});
  }

  function waitIceGathering(pc, timeoutMs) {
    return new Promise((resolve) => {
      if (pc.iceGatheringState === 'complete') return resolve();
      const done = () => {
        pc.removeEventListener('icegatheringstatechange', onChange);
        resolve();
      };
      const onChange = () => { if (pc.iceGatheringState === 'complete') done(); };
      pc.addEventListener('icegatheringstatechange', onChange);
      setTimeout(done, timeoutMs);
    });
  }

  function destroyWhep(player) {
    if (player.whepLocation) {
      try { fetch(player.whepLocation, { method: 'DELETE', keepalive: true }); } catch (_) {}
      player.whepLocation = null;
    }
    if (player.pc) {
      const pc = player.pc;
      player.pc = null;
      try { pc.ontrack = null; } catch (_) {}
      try { pc.close(); } catch (_) {}
    }
    if (player.video && player.video.srcObject) {
      try { player.video.srcObject = null; } catch (_) {}
    }
    player.usingWebrtc = false;
  }

  function destroyHls(player) {
    if (player && player.hls) {
      try { player.hls.destroy(); } catch (_) {}
      player.hls = null;
    }
  }

  function destroyStreams(player) {
    destroyWhep(player);
    destroyHls(player);
  }

  function destroyPlayer(name) {
    const player = players.get(name);
    if (!player) return;
    clearTimeout(player.retryTimer);
    destroyStreams(player);
    players.delete(name);
  }

  function scheduleRetry(name, reason) {
    const player = players.get(name);
    if (!player) return;
    setStatus(tiles.get(name), reason + ' — tentando de novo...', 'is-error');
    clearTimeout(player.retryTimer);
    player.retry = Math.min(player.retry + 1, 6);
    player.retryTimer = setTimeout(() => startStream(name), RETRY_DELAY_MS * player.retry);
  }

  // ── Tela cheia ────────────────────────────────────────────────────────────
  function currentFullscreenEl() {
    return document.fullscreenElement || document.webkitFullscreenElement || null;
  }

  function toggleFullscreen(el) {
    const active = currentFullscreenEl();
    if (active === el) {
      (document.exitFullscreen || document.webkitExitFullscreen).call(document);
      return;
    }
    fsActive = true; // marca antes para o renderGrid nao remontar a grade
    const req = el.requestFullscreen || el.webkitRequestFullscreen;
    if (!req) { fsActive = false; return; }
    try {
      const res = req.call(el);
      if (res && typeof res.catch === 'function') res.catch(() => { fsActive = false; });
    } catch (_) {
      fsActive = false;
    }
  }

  function onFsChange() {
    fsActive = !!currentFullscreenEl();
    if (!fsActive) renderGrid();
  }
  document.addEventListener('fullscreenchange', onFsChange);
  document.addEventListener('webkitfullscreenchange', onFsChange);

  // ── Painel de seleção ─────────────────────────────────────────────────────
  function renderPickList() {
    pickList.innerHTML = '';
    allCams.forEach((cam) => {
      const label = document.createElement('label');
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = selected.includes(cam.name);
      cb.addEventListener('change', () => {
        if (cb.checked) { if (!selected.includes(cam.name)) selected.push(cam.name); }
        else selected = selected.filter((n) => n !== cam.name);
        saveSelection();
        renderGrid();
      });
      const span = document.createElement('span');
      span.textContent = cam.name;
      label.append(cb, span);
      pickList.appendChild(label);
    });
  }

  pickBtn.addEventListener('click', () => pickPanel.classList.toggle('hidden'));
  closePickBtn.addEventListener('click', () => pickPanel.classList.add('hidden'));
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') pickPanel.classList.add('hidden'); });

  pickAllBtn.addEventListener('click', () => {
    selected = allCams.map((c) => c.name);
    saveSelection(); renderPickList(); renderGrid();
  });
  pickNoneBtn.addEventListener('click', () => {
    selected = [];
    saveSelection(); renderPickList(); renderGrid();
  });

  fsAllBtn.addEventListener('click', () => toggleFullscreen(document.documentElement));

  // ── Resize ────────────────────────────────────────────────────────────────
  let resizeTimer = null;
  window.addEventListener('resize', () => {
    if (fsActive || currentFullscreenEl()) return;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(renderGrid, 150);
  });

  // ── Init ──────────────────────────────────────────────────────────────────
  async function init() {
    try {
      const res = await fetch('/api/live');
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      allCams = (data.cameras || []).filter((c) => c.hls_url);

      if (allCams.length === 0) {
        emptyMsg.classList.remove('hidden');
        emptyMsg.innerHTML = '<p>Nenhuma câmera configurada no cameras.json.</p>';
        return;
      }

      const saved = loadSelection();
      const valid = new Set(allCams.map((c) => c.name));
      selected = saved ? saved.filter((n) => valid.has(n)) : allCams.map((c) => c.name);

      renderPickList();
      renderGrid();
    } catch (err) {
      emptyMsg.classList.remove('hidden');
      emptyMsg.innerHTML = `<p>Erro ao carregar câmeras: ${err.message}</p>`;
    }
  }

  init();
});
