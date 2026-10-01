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

    const fsBtn = document.createElement('button');
    fsBtn.className = 'mc-btn';
    fsBtn.title = 'Tela cheia';
    fsBtn.textContent = '⛶';
    fsBtn.addEventListener('click', (e) => { e.stopPropagation(); toggleFullscreen(tile); });

    tile.append(video, status, fsBtn);

    const player = { video, hls: null, retry: 0, retryTimer: null };
    players.set(cam.name, player);

    video.addEventListener('playing', () => { player.retry = 0; });
    video.addEventListener('error', () => { if (!player.hls) scheduleRetry(cam.name, 'erro'); });

    tile.addEventListener('dblclick', () => toggleFullscreen(tile));

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

  function setStatus(tile, text, cls) {
    const el = tile.querySelector('.mc-status');
    if (!el) return;
    el.textContent = text;
    el.className = 'mc-status' + (cls ? ' ' + cls : '');
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

  // ── Stream HLS ────────────────────────────────────────────────────────────
  function startStream(name) {
    const cam = allCams.find((c) => c.name === name);
    const player = players.get(name);
    if (!cam || !player) return;
    destroyHls(player);
    setStatus(tiles.get(name), '');

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
      setStatus(tiles.get(name), 'HLS não suportado', 'is-error');
      return;
    }
    player.video.play().catch(() => {});
  }

  function destroyHls(player) {
    if (player && player.hls) {
      try { player.hls.destroy(); } catch (_) {}
      player.hls = null;
    }
  }

  function destroyPlayer(name) {
    const player = players.get(name);
    if (!player) return;
    clearTimeout(player.retryTimer);
    destroyHls(player);
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
