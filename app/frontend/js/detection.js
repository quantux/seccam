document.addEventListener('DOMContentLoaded', () => {
  const $ = (id) => document.getElementById(id);
  const camSelect = $('camSelect');
  const canvas = $('roiCanvas');
  const ctx = canvas.getContext('2d');
  const roiEmpty = $('roiEmpty');
  const roiReadout = $('roiReadout');
  const camList = $('camList');
  const saveMsg = $('saveMsg');

  let settings = null;
  let currentImage = null;      // HTMLImageElement carregada
  let drag = null;              // {x0,y0,x1,y1} em pixels da imagem natural
  let currentRoi = null;        // roi normalizada da câmera selecionada

  // ── Carrega configuração ──────────────────────────────────────────────────
  async function loadSettings() {
    const res = await fetch('/api/detection');
    settings = await res.json();
    if (!settings.cameras) settings.cameras = [];

    $('dwell').value = settings.dwell_seconds;
    $('tolerance').value = settings.absence_tolerance_seconds;
    $('threshold').value = settings.probability_threshold;
    $('pollFps').value = settings.poll_fps;
    $('ntfyUrl').value = settings.notify.url || 'https://ntfy.sh';
    $('ntfyTopic').value = settings.notify.topic || '';
    $('ntfyToken').value = settings.notify.token || '';
    $('ntfyPriority').value = String(settings.notify.priority || '4');

    renderCamList();
    renderCamSelect();
  }

  function renderCamSelect() {
    const prev = camSelect.value;
    camSelect.innerHTML = '';
    if (settings.cameras.length === 0) {
      camSelect.innerHTML = '<option value="">Nenhuma câmera cadastrada</option>';
      return;
    }
    settings.cameras.forEach((cam, idx) => {
      const opt = document.createElement('option');
      opt.value = String(idx);
      opt.textContent = `${cam.name}${cam.enabled ? '' : ' (desativada)'}`;
      camSelect.appendChild(opt);
    });
    if (prev && settings.cameras[prev]) camSelect.value = prev;
  }

  function renderCamList() {
    camList.innerHTML = '';
    if (settings.cameras.length === 0) {
      camList.innerHTML = '<p class="status-msg">Nenhuma câmera. Adicione abaixo.</p>';
      return;
    }
    settings.cameras.forEach((cam, idx) => {
      const row = document.createElement('div');
      row.className = 'cam-row';
      const roi = cam.roi ? `ROI ${(cam.roi.x * 100).toFixed(0)}%,${(cam.roi.y * 100).toFixed(0)}% ` +
        `${(cam.roi.w * 100).toFixed(0)}x${(cam.roi.h * 100).toFixed(0)}%` : 'ROI (frame inteiro)';
      row.innerHTML = `
        <label class="cam-toggle">
          <input type="checkbox" ${cam.enabled ? 'checked' : ''} data-idx="${idx}">
          <span class="cam-name">${cam.name}</span>
        </label>
        <span class="cam-source" title="${cam.source}">${cam.source}</span>
        <span class="cam-roi">${roi}</span>
        <button class="cam-remove" data-idx="${idx}" title="Remover">✕</button>`;

      row.querySelector('input[type=checkbox]').addEventListener('change', (e) => {
        settings.cameras[idx].enabled = e.target.checked;
        renderCamSelect();
      });
      row.querySelector('.cam-remove').addEventListener('click', () => {
        settings.cameras.splice(idx, 1);
        renderCamList();
        renderCamSelect();
        clearCanvas();
      });
      camList.appendChild(row);
    });
  }

  // ── ROI no canvas ─────────────────────────────────────────────────────────
  function draw() {
    if (!currentImage) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(currentImage, 0, 0, canvas.width, canvas.height);

    const r = drag || (currentRoi ? {
      x0: currentRoi.x * canvas.width,
      y0: currentRoi.y * canvas.height,
      x1: (currentRoi.x + currentRoi.w) * canvas.width,
      y1: (currentRoi.y + currentRoi.h) * canvas.height,
    } : null);

    if (r) {
      const x = Math.min(r.x0, r.x1), y = Math.min(r.y0, r.y1);
      const w = Math.abs(r.x1 - r.x0), h = Math.abs(r.y1 - r.y0);
      ctx.save();
      ctx.fillStyle = 'rgba(0, 0, 0, 0.45)';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.clearRect(x, y, w, h);
      ctx.drawImage(currentImage, x, y, w, h, x, y, w, h);
      ctx.strokeStyle = '#3b82f6';
      ctx.lineWidth = Math.max(2, canvas.width / 400);
      ctx.strokeRect(x, y, w, h);
      ctx.restore();
    }
  }

  function pointerPos(e) {
    const rect = canvas.getBoundingClientRect();
    return {
      x: (e.clientX - rect.left) * (canvas.width / rect.width),
      y: (e.clientY - rect.top) * (canvas.height / rect.height),
    };
  }

  canvas.addEventListener('mousedown', (e) => {
    if (!currentImage) return;
    const p = pointerPos(e);
    drag = { x0: p.x, y0: p.y, x1: p.x, y1: p.y };
    draw();
  });
  window.addEventListener('mousemove', (e) => {
    if (!drag) return;
    const p = pointerPos(e);
    drag.x1 = p.x; drag.y1 = p.y;
    draw();
    updateReadout();
  });
  window.addEventListener('mouseup', () => {
    if (!drag) return;
    const idx = parseInt(camSelect.value, 10);
    if (!isNaN(idx) && settings.cameras[idx]) {
      const x = Math.min(drag.x0, drag.x1) / canvas.width;
      const y = Math.min(drag.y0, drag.y1) / canvas.height;
      const w = Math.abs(drag.x1 - drag.x0) / canvas.width;
      const h = Math.abs(drag.y1 - drag.y0) / canvas.height;
      if (w > 0.01 && h > 0.01) {
        settings.cameras[idx].roi = {
          x: +x.toFixed(4), y: +y.toFixed(4), w: +w.toFixed(4), h: +h.toFixed(4),
        };
        currentRoi = settings.cameras[idx].roi;
        renderCamList();
      }
    }
    drag = null;
    draw();
    updateReadout();
  });

  function updateReadout() {
    const idx = parseInt(camSelect.value, 10);
    if (isNaN(idx) || !settings.cameras[idx]) { roiReadout.textContent = ''; return; }
    const roi = settings.cameras[idx].roi;
    if (!roi) { roiReadout.textContent = 'Sem ROI definida (será usado o frame inteiro).'; return; }
    roiReadout.textContent = `ROI: x=${(roi.x * 100).toFixed(1)}% y=${(roi.y * 100).toFixed(1)}% ` +
      `w=${(roi.w * 100).toFixed(1)}% h=${(roi.h * 100).toFixed(1)}%`;
  }

  function clearCanvas() {
    currentImage = null;
    currentRoi = null;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    roiEmpty.style.display = 'flex';
  }

  async function loadFrame() {
    const idx = parseInt(camSelect.value, 10);
    if (isNaN(idx) || !settings.cameras[idx]) { saveMsg.textContent = 'Selecione uma câmera.'; return; }
    const cam = settings.cameras[idx];
    saveMsg.textContent = 'Capturando frame...';
    try {
      const res = await fetch(`/api/detection/snapshot?source=${encodeURIComponent(cam.source)}&t=${Date.now()}`);
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const img = new Image();
      await new Promise((resolve, reject) => { img.onload = resolve; img.onerror = reject; img.src = url; });
      currentImage = img;
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      currentRoi = cam.roi || null;
      roiEmpty.style.display = 'none';
      draw();
      updateReadout();
      saveMsg.textContent = `Frame de ${cam.name} carregado.`;
    } catch (err) {
      saveMsg.textContent = `Erro: ${err.message}`;
    }
  }

  // ── Salvar / testar ───────────────────────────────────────────────────────
  function collect() {
    return {
      ...settings,
      dwell_seconds: Number($('dwell').value),
      absence_tolerance_seconds: Number($('tolerance').value),
      probability_threshold: Number($('threshold').value),
      poll_fps: Number($('pollFps').value),
      notify: {
        url: $('ntfyUrl').value.trim() || 'https://ntfy.sh',
        topic: $('ntfyTopic').value.trim(),
        token: $('ntfyToken').value.trim() || null,
        priority: $('ntfyPriority').value,
        tags: (settings.notify && settings.notify.tags) || 'rotating_light',
      },
    };
  }

  async function save() {
    saveMsg.textContent = 'Salvando...';
    try {
      const res = await fetch('/api/detection', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(collect()),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      settings = await res.json();
      saveMsg.textContent = '✅ Salvo. Reinicie o container para aplicar nas threads.';
      renderCamList();
    } catch (err) {
      saveMsg.textContent = `❌ ${err.message}`;
    }
  }

  async function testNotify() {
    saveMsg.textContent = 'Enviando teste...';
    try {
      const res = await fetch('/api/detection/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ notify: collect().notify }),
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
      saveMsg.textContent = '✅ Teste enviado. Confira o app ntfy.';
    } catch (err) {
      saveMsg.textContent = `❌ ${err.message}`;
    }
  }

  $('loadBtn').addEventListener('click', loadFrame);
  $('clearRoiBtn').addEventListener('click', () => {
    const idx = parseInt(camSelect.value, 10);
    if (!isNaN(idx) && settings.cameras[idx]) { settings.cameras[idx].roi = null; currentRoi = null; renderCamList(); }
    draw();
    updateReadout();
  });
  $('saveBtn').addEventListener('click', save);
  $('testBtn').addEventListener('click', testNotify);
  $('addCamBtn').addEventListener('click', () => {
    const name = $('newName').value.trim();
    const source = $('newSource').value.trim();
    if (!name || !source) { saveMsg.textContent = 'Informe nome e fonte da câmera.'; return; }
    settings.cameras.push({ name, source, enabled: true, roi: null });
    $('newName').value = ''; $('newSource').value = '';
    renderCamList();
    renderCamSelect();
  });

  camSelect.addEventListener('change', () => {
    const idx = parseInt(camSelect.value, 10);
    if (!isNaN(idx) && settings.cameras[idx]) {
      currentRoi = settings.cameras[idx].roi || null;
      if (currentImage) { draw(); updateReadout(); }
    }
  });

  loadSettings().catch((err) => { saveMsg.textContent = `Erro ao carregar: ${err.message}`; });
});
