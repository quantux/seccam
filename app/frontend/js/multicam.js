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
  const ptzPanel = document.getElementById('ptzPanel');
  const ptzTitle = document.getElementById('ptzTitle');
  const ptzStatusEl = document.getElementById('ptzStatus');
  const ptzPresetsEl = document.getElementById('ptzPresets');
  const ptzCloseBtn = document.getElementById('ptzCloseBtn');
  const ptzRefreshBtn = document.getElementById('ptzRefreshBtn');

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
  let pendingRender = false; // mudou a selecao durante fullscreen de um tile
  let ptzCamera = null;      // camera com o painel PTZ aberto
  let ptzPresetList = [];
  let ptzRenderedSignature = '';
  let ptzSyncedAt = 0;
  let ptzMoveTimer = null;
  let ptzPresetTimer = null;
  let ptzMoveBusy = false;
  let ptzPresetBusy = false;
  let ptzLoadSeq = 0;

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
  // A grade e montada por linhas flex. Cada linha ocupa a largura toda e divide
  // o espaco igualmente entre suas cameras; uma linha com 1 camera, portanto,
  // faz essa camera ocupar 100% da largura (ex.: 5 cameras => 2x2 + 1 embaixo).
  const VIDEO_ASPECT = 16 / 9;
  // Com 2 cameras, so usa lado a lado quando a tela e claramente mais larga
  // que alta. Isso da uma folga extra antes de trocar para o modo empilhado
  // (ex.: janela ~960x950 do Brave ja fica empilhada).
  const SIDE_BY_SIDE_MIN_ASPECT = 1.2;

  // Para uma dada quantidade de colunas, monta linhas cheias e deixa o
  // excedente na ultima linha (que fica mais larga).
  function buildRows(n, cols) {
    const rowCount = Math.ceil(n / cols);
    const rows = new Array(rowCount).fill(cols);
    rows[rowCount - 1] = n - cols * (rowCount - 1);
    return rows;
  }

  // Área realmente ocupada pelos vídeos (object-fit: contain) em uma grade
  // com linhas de altura igual. Usamos isso para escolher o layout que mostra
  // mais imagem, em vez de apenas comparar a proporção das células.
  function layoutVideoArea(rows, W, H) {
    const rowH = H / rows.length;
    let area = 0;
    for (const count of rows) {
      const tileW = W / count;
      const videoW = Math.min(tileW, rowH * VIDEO_ASPECT);
      area += count * videoW * (videoW / VIDEO_ASPECT);
    }
    return area;
  }

  function computeRows(n, W, H) {
    if (n <= 0) return [];
    if (n === 1) return [1];

    const aspect = W / Math.max(H, 1);

    // 2 cameras: empilha a menos que a tela seja bem mais larga que alta.
    if (n === 2) return aspect >= SIDE_BY_SIDE_MIN_ASPECT ? [2] : [1, 1];

    // 3+ cameras: testa todas as quantidades de colunas e fica com a que
    // maximiza a area visivel dos videos. Mantemos as linhas equilibradas
    // (diferenca de no maximo 1 camera), para que o excedente vire uma linha
    // final isolada e larga, em vez de uma camera minuscula ao lado de uma
    // linha cheia (ex.: 5 => [2,2,1], e nao [4,1]).
    let best = null;
    for (let cols = 1; cols <= n; cols++) {
      const rows = buildRows(n, cols);
      if (Math.max(...rows) - Math.min(...rows) > 1) continue;
      const area = layoutVideoArea(rows, W, H);
      if (!best || area > best.area) best = { rows, area };
    }
    return best ? best.rows : [n];
  }

  // Só não remontamos a grade quando um TILE está em tela cheia (mover o
  // elemento no DOM faria o navegador sair da tela cheia). Quando o elemento
  // em fullscreen é a própria grade (<html>), remontar é seguro.
  // A checagem é feita direto no DOM: nunca usamos uma flag que possa ficar
  // presa se o navegador não disparar o evento fullscreenchange.
  function canRenderNow() {
    const el = currentFullscreenEl();
    return !el || el === document.documentElement;
  }

  function renderGrid() {
    if (!selected) return;
    if (!canRenderNow()) { pendingRender = true; return; }
    pendingRender = false;
    const valid = new Set(allCams.map((c) => c.name));
    selected = selected.filter((n) => valid.has(n));
    if (ptzCamera && !selected.includes(ptzCamera)) closePtz();

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

    const rows = computeRows(
      selected.length,
      window.visualViewport ? window.visualViewport.width : window.innerWidth,
      window.visualViewport ? window.visualViewport.height : window.innerHeight
    );
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

    let ptzBtn = null;
    if (cam.ptz) {
      ptzBtn = document.createElement('button');
      ptzBtn.className = 'mc-btn mc-ptz-open';
      ptzBtn.title = 'Pan/tilt' + (cam.ptz_label ? ' — ' + cam.ptz_label : '');
      ptzBtn.textContent = '🕹';
      ptzBtn.addEventListener('click', (e) => { e.stopPropagation(); openPtz(cam); });
      ptzBtn.addEventListener('dblclick', (e) => e.stopPropagation());
    }

    tile.append(video, status, audioBtn, fsBtn);
    if (ptzBtn) tile.append(ptzBtn);

    const player = { video, hls: null, pc: null, whepLocation: null, usingWebrtc: false, retry: 0, retryTimer: null };
    players.set(cam.name, player);

    const zoom = enableZoom(tile, video);

    video.addEventListener('playing', () => { player.retry = 0; });
    video.addEventListener('error', () => { if (!player.hls && !player.pc) scheduleRetry(cam.name, 'erro'); });

    // Duplo toque/clique: se ampliado, volta ao normal; senão, tela cheia.
    tile.addEventListener('dblclick', (e) => {
      if (e.target.closest('.mc-btn')) return;
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
  // Cada câmera tem mute independente: o usuário pode ligar o som de várias
  // (ou de todas) ao mesmo tempo. O clique é o gesto exigido pelo navegador
  // para liberar áudio no autoplay.
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

    v.muted = !v.muted; // alterna só esta câmera
    if (!v.muted) {
      v.volume = 1;
      v.play().catch(() => {});
    }

    updateAudioBtn(tiles.get(name), v.muted);
  }

  // ── PTZ (pan/tilt) ────────────────────────────────────────────────────────
  function apiError(data, res) {
    if (data && typeof data.detail === 'string') return data.detail;
    if (data && Array.isArray(data.detail)) {
      return data.detail.map((d) => d.msg || JSON.stringify(d)).join('; ');
    }
    return 'HTTP ' + res.status;
  }

  function setPtzStatus(text, isError) {
    if (!ptzStatusEl) return;
    ptzStatusEl.textContent = text || '';
    ptzStatusEl.classList.toggle('is-error', !!isError);
  }

  const PTZ_SYNC_MS = 30000;

  function formatPtzTime(ts) {
    return new Date(ts).toLocaleTimeString('pt-BR', { hour12: false });
  }

  function updatePtzSyncStatus() {
    if (!ptzSyncedAt) return;
    if (!ptzPresetList.length) {
      setPtzStatus('Sem presets cadastrados no app Tapo.');
    } else {
      setPtzStatus('Presets sincronizados às ' + formatPtzTime(ptzSyncedAt) + '.');
    }
  }

  function stopPtzPresetSync() {
    if (ptzPresetTimer) {
      clearInterval(ptzPresetTimer);
      ptzPresetTimer = null;
    }
  }

  function startPtzPresetSync() {
    stopPtzPresetSync();
    ptzPresetTimer = setInterval(() => {
      if (ptzCamera && !ptzPanel.classList.contains('hidden')) loadPtzPresets(true);
    }, PTZ_SYNC_MS);
  }

  function openPtz(cam) {
    if (ptzCamera === cam.name && !ptzPanel.classList.contains('hidden')) {
      closePtz();
      return;
    }
    ptzCamera = cam.name;
    ptzTitle.textContent = cam.ptz_label || cam.name;
    ptzPanel.classList.remove('hidden');
    pickPanel.classList.add('hidden');
    ptzPresetList = [];
    ptzRenderedSignature = '';
    ptzSyncedAt = 0;
    ptzPresetsEl.innerHTML = '';
    setPtzStatus('Carregando presets...');
    loadPtzPresets(true);
    startPtzPresetSync();
  }

  function closePtz() {
    stopPtzMove();
    stopPtzPresetSync();
    ptzCamera = null;
    ptzLoadSeq += 1; // ignora respostas pendentes
    ptzPanel.classList.add('hidden');
    ptzPresetsEl.innerHTML = '';
    ptzPresetList = [];
    ptzRenderedSignature = '';
    ptzSyncedAt = 0;
    setPtzStatus('');
  }

  function renderPtzPresets() {
    ptzPresetsEl.innerHTML = '';
    if (!ptzPresetList.length) return;
    ptzPresetList.forEach((preset) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'mc-mini mc-ptz-preset';
      btn.textContent = preset.name || ('Preset ' + preset.id);
      btn.title = 'Ir para o preset ' + (preset.name || preset.id);
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        sendPtzPreset(preset.id);
      });
      ptzPresetsEl.appendChild(btn);
    });
  }

  async function loadPtzPresets(refresh) {
    if (!ptzCamera) return;
    const name = ptzCamera;
    const seq = ++ptzLoadSeq;
    try {
      const res = await fetch(
        `/api/ptz/${encodeURIComponent(name)}/presets?refresh=${refresh ? 1 : 0}`
      );
      const data = await res.json().catch(() => ({}));
      if (seq !== ptzLoadSeq || ptzCamera !== name) return;
      if (!res.ok) throw new Error(apiError(data, res));
      const next = data.presets || [];
      const signature = JSON.stringify(next);
      if (signature !== ptzRenderedSignature) {
        ptzPresetList = next;
        ptzRenderedSignature = signature;
        renderPtzPresets();
      }
      ptzSyncedAt = Date.now();
      if (!ptzMoveBusy && !ptzPresetBusy) updatePtzSyncStatus();
    } catch (err) {
      if (seq !== ptzLoadSeq || ptzCamera !== name) return;
      // Mantem a ultima lista boa; apenas informa o erro.
      if (!ptzPresetList.length) renderPtzPresets();
      setPtzStatus(err.message || 'Falha ao carregar presets.', true);
    }
  }

  async function sendPtzMove(direction) {
    if (!ptzCamera || ptzMoveBusy) return;
    const name = ptzCamera;
    ptzMoveBusy = true;
    try {
      const res = await fetch(`/api/ptz/${encodeURIComponent(name)}/move`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ direction }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const err = new Error(apiError(data, res));
        err.status = res.status;
        throw err;
      }
      setPtzStatus('Movendo...');
    } catch (err) {
      setPtzStatus(err.message || 'Falha ao mover.', true);
      // Rate-limit ou comando invalido: para o repeat ate o usuario tentar de novo.
      if (err.status === 429 || err.status === 400 || err.status === 409) stopPtzMove();
    } finally {
      ptzMoveBusy = false;
    }
  }

  function startPtzMove(direction) {
    stopPtzMove();
    sendPtzMove(direction);
    ptzMoveTimer = setInterval(() => sendPtzMove(direction), 450);
  }

  function stopPtzMove() {
    if (ptzMoveTimer) {
      clearInterval(ptzMoveTimer);
      ptzMoveTimer = null;
    }
  }

  async function sendPtzPreset(presetId) {
    if (!ptzCamera || ptzPresetBusy) return;
    const name = ptzCamera;
    ptzPresetBusy = true;
    setPtzStatus('Movendo para o preset...');
    try {
      const res = await fetch(`/api/ptz/${encodeURIComponent(name)}/preset`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: String(presetId) }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(apiError(data, res));
      setPtzStatus('Pronto.');
    } catch (err) {
      setPtzStatus(err.message || 'Falha ao ir para o preset.', true);
    } finally {
      ptzPresetBusy = false;
    }
  }

  // D-pad: um comando imediato e repete enquanto o botao estiver pressionado.
  document.querySelectorAll('.mc-ptz-btn[data-dir]').forEach((btn) => {
    const direction = btn.dataset.dir;
    btn.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      e.stopPropagation();
      try { btn.setPointerCapture(e.pointerId); } catch (_) {}
      startPtzMove(direction);
    });
    ['pointerup', 'pointercancel', 'pointerleave'].forEach((evt) => {
      btn.addEventListener(evt, (e) => { e.stopPropagation(); stopPtzMove(); });
    });
  });
  ptzCloseBtn.addEventListener('click', closePtz);
  ptzRefreshBtn.addEventListener('click', () => loadPtzPresets(true));
  ptzPanel.addEventListener('contextmenu', (e) => e.preventDefault());

  // Ao voltar para a aba, sincroniza os presets de novo (o app Tapo pode ter
  // mudado algo enquanto o painel estava em segundo plano).
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && ptzCamera && !ptzPanel.classList.contains('hidden')) {
      loadPtzPresets(true);
    }
  });

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
      const exit = document.exitFullscreen || document.webkitExitFullscreen;
      if (exit) exit.call(document);
      return;
    }
    const req = el.requestFullscreen || el.webkitRequestFullscreen;
    if (!req) return;
    try {
      const res = req.call(el);
      if (res && typeof res.catch === 'function') res.catch(() => {});
    } catch (_) {}
  }

  function onFsChange() {
    // Ao entrar no fullscreen da grade ou sair de qualquer fullscreen, aplica
    // mudancas que ficaram pendentes (ex.: cameras ocultadas/exibidas).
    if (canRenderNow()) renderGrid();
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
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      pickPanel.classList.add('hidden');
      closePtz();
    }
  });

  pickAllBtn.addEventListener('click', () => {
    selected = allCams.map((c) => c.name);
    saveSelection(); renderPickList(); renderGrid();
  });
  pickNoneBtn.addEventListener('click', () => {
    selected = [];
    saveSelection(); renderPickList(); renderGrid();
  });

  fsAllBtn.addEventListener('click', () => toggleFullscreen(document.documentElement));

  // ── Resize / mudança de orientação ────────────────────────────────────────
  let resizeTimer = null;
  function scheduleRelayout() {
    if (!canRenderNow()) { pendingRender = true; return; }
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(renderGrid, 150);
  }
  window.addEventListener('resize', scheduleRelayout);
  window.addEventListener('orientationchange', scheduleRelayout);
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', scheduleRelayout);
  }

  // ── Controles: auto-ocultar por inatividade ───────────────────────────────
  const CONTROLS_IDLE_MS = 3000;
  let controlsTimer = null;
  function showControls() {
    document.body.classList.remove('controls-hidden');
    clearTimeout(controlsTimer);
    controlsTimer = setTimeout(() => {
      document.body.classList.add('controls-hidden');
    }, CONTROLS_IDLE_MS);
  }
  ['pointermove', 'pointerdown', 'keydown', 'wheel', 'touchstart'].forEach((evt) => {
    window.addEventListener(evt, showControls, { passive: true });
  });
  showControls();

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
