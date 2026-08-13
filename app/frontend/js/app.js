document.addEventListener('DOMContentLoaded', () => {
  const cameraSelect = document.getElementById('cameraSelect');
  const dateSelect = document.getElementById('dateSelect');
  const playerPlaceholder = document.getElementById('playerPlaceholder');
  const placeholderText = document.getElementById('placeholderText');
  const loadingSpinner = document.getElementById('loadingSpinner');
  const statusMessage = document.getElementById('statusMessage');

  const exportForm = document.getElementById('exportForm');
  const exportStart = document.getElementById('exportStart');
  const exportEnd = document.getElementById('exportEnd');
  const exportBtn = document.getElementById('exportBtn');
  const exportBtnLabel = document.getElementById('exportBtnLabel');
  const exportMsg = document.getElementById('exportMsg');
  const exportProgress = document.getElementById('exportProgress');
  const setStartBtn = document.getElementById('setStartBtn');
  const setEndBtn = document.getElementById('setEndBtn');

  let currentPlaylistUrl = null;
  let playlistStartTime = null; // "HH:MM:SS" do primeiro segmento do dia

  // Flag para evitar loop: quando recriamos o player internamente no seek,
  // não queremos que o evento 'seeking' dispare outro reload.
  let isSeeking = false;

  // ─── Inicialização do Video.js Player ─────────────────────────────────────
  const player = videojs('videoPlayer', {
    controls: true,
    autoplay: false,
    preload: 'auto',
    responsive: true,
    fluid: true,
    playbackRates: [0.5, 1, 1.5, 2, 4, 8],
    html5: {
      vhs: {
        overrideNative: true,
        enableLowInitialPlaylist: true,
        smoothQualityChange: true,
        // Buffer e tolerâncias otimizados para VOD longo com descontinuidades
        // em rede remota (Raspberry Pi 4, ~150Mbps)
        backBufferLength: 30,
        maxBufferLength: 30,
        maxMaxBufferLength: 120,
        maxBufferSize: 30 * 1024 * 1024,
        maxBufferHole: 2.0,
      },
      nativeAudioTracks: false,
      nativeVideoTracks: false
    }
  });

  // Eventos de estado do Video.js
  player.on('playing', () => {
    showPlaceholder(false);
    showStatus('Reproduzindo vídeo.');
  });

  player.on('waiting', () => {
    showStatus('Carregando buffer...');
  });

  player.on('error', () => {
    const err = player.error();
    console.error('Erro no Video.js:', err);
    showStatus('Erro ao carregar o vídeo.');
  });

  // ─── Seek confiável: intercepta o seek do usuário e recria o player ──────
  // O VHS (Video.js) tem comportamento instável ao fazer seek em VOD longo com
  // muitas descontinuidades: o vídeo pula para posições erradas ou trava
  // infinitamente ("loading"). A solução comprovada é recarregar a playlist
  // e pular para a posição alvo, em vez de deixar o seek interno falhar.
  let seekDebounce = null;
  let lastSeekReload = 0;
  const SEEK_COOLDOWN_MS = 3000;

  player.on('seeking', () => {
    if (!currentPlaylistUrl) return;

    // Ignora os eventos de 'seeking' que o próprio VHS dispara durante o
    // buffering/gap-seeking (não são seeks do usuário). Sem este cooldown,
    // o player recarrega em loop infinito.
    if (isSeeking) return;
    if (Date.now() - lastSeekReload < SEEK_COOLDOWN_MS) return;

    clearTimeout(seekDebounce);
    const targetTime = player.currentTime();

    // Pequeno debounce para não disparar em cada pixel do scrubbing
    seekDebounce = setTimeout(() => {
      performSeek(targetTime);
    }, 400);
  });

  function performSeek(targetSeconds) {
    if (!currentPlaylistUrl) return;

    console.log(`[seek] Recarregando em ${targetSeconds.toFixed(1)}s`);
    isSeeking = true;
    lastSeekReload = Date.now();
    showStatus('Buscando posição...');

    const wasPlaying = !player.paused();

    reloadAtPosition(targetSeconds, () => {
      isSeeking = false;
      if (wasPlaying) {
        player.play().catch(() => {});
      }
    });
  }

  // Recarrega a playlist e posiciona o player na posição alvo.
  // IMPORTANTE: seta isSeeking=true antes de player.src(), porque o próprio
  // player.src()/currentTime() dispara o evento 'seeking' — sem isso vira loop.
  function reloadAtPosition(targetSeconds, onReady) {
    player.one('loadedmetadata', () => {
      player.currentTime(targetSeconds);
      // O VHS recalcula a timeline ao carregar o fragmento alvo
      if (onReady) onReady();
    });

    // Garante que os seeks internos desta recarga sejam ignorados
    isSeeking = true;
    player.src({
      src: currentPlaylistUrl,
      type: 'application/x-mpegURL'
    });
  }

  // ─── Estado inicial ────────────────────────────────────────────────────────
  showPlaceholder(true, 'Selecione uma câmera e uma data para assistir', false);
  fetchCameras();

  // ─── 1. Câmeras ────────────────────────────────────────────────────────────
  async function fetchCameras() {
    try {
      showStatus('Carregando lista de câmeras...');
      const res = await fetch('/api/cameras');
      const cameras = await res.json();
      cameraSelect.innerHTML = '<option value="">Selecione uma câmera</option>';

      if (cameras.length === 0) {
        showStatus('Nenhuma câmera configurada.');
        showPlaceholder(true, 'Nenhuma câmera cadastrada.', false);
        return;
      }
      cameras.forEach(cam => {
        const opt = document.createElement('option');
        opt.value = cam.name;
        opt.textContent = cam.name;
        cameraSelect.appendChild(opt);
      });
      showStatus(`Encontradas ${cameras.length} câmera(s).`);
    } catch (err) {
      console.error(err);
      showStatus('Erro ao conectar ao servidor de câmeras.');
    }
  }

  // ─── 2. Seleção de câmera ──────────────────────────────────────────────────
  cameraSelect.addEventListener('change', async () => {
    const selectedCamera = cameraSelect.value;
    dateSelect.value = '';
    dateSelect.disabled = true;
    exportBtn.disabled = true;

    if (!selectedCamera) {
      showPlaceholder(true, 'Selecione uma câmera e uma data para assistir', false);
      return;
    }

    try {
      showStatus('Buscando datas disponíveis...');
      const res = await fetch(`/api/days?camera=${encodeURIComponent(selectedCamera)}`);
      const data = await res.json();

      if (!data.dates || data.dates.length === 0) {
        showStatus('Nenhuma gravação encontrada para esta câmera.');
        showPlaceholder(true, `Aguardando o primeiro segmento de ${selectedCamera}...`, false);
        return;
      }

      dateSelect.disabled = false;
      exportBtn.disabled = false;
      dateSelect.min = data.dates[data.dates.length - 1];
      dateSelect.max = data.dates[0];
      dateSelect.value = data.dates[0];
      showStatus(`Gravações encontradas para ${data.dates.length} dia(s).`);
      loadPlaylist();
    } catch (err) {
      console.error(err);
      showStatus('Erro ao buscar datas de gravação.');
    }
  });

  // ─── 3. Seleção de data ────────────────────────────────────────────────────
  dateSelect.addEventListener('change', () => {
    if (dateSelect.value && cameraSelect.value) {
      exportBtn.disabled = false;
      loadPlaylist();
    }
  });

  // ─── 4. Carrega a playlist HLS no Video.js ────────────────────────────────
  function loadPlaylist() {
    const camera = cameraSelect.value;
    const date = dateSelect.value;
    if (!camera || !date) return;

    currentPlaylistUrl = `/api/playlist.m3u8?camera=${encodeURIComponent(camera)}&date=${encodeURIComponent(date)}`;
    playlistStartTime = null;

    showPlaceholder(true, 'Carregando vídeo...', true);

    // Extrai o horário inicial da playlist para a ferramenta de exportação
    fetch(currentPlaylistUrl)
      .then(res => res.text())
      .then(text => {
        const match = text.match(/file=(\d{4}-\d{2}-\d{2}_)?(\d{2})-(\d{2})-(\d{2})\.ts/);
        if (match) {
          playlistStartTime = `${match[2]}:${match[3]}:${match[4]}`;
        }
      })
      .catch(() => {});

    reloadAtPosition(0, () => {
      isSeeking = false;
      showPlaceholder(false);
      player.play().then(() => {
        showStatus('Vídeo pronto para reprodução.');
      }).catch(err => {
        console.log('Autoplay não iniciado automaticamente:', err);
        showStatus('Vídeo pronto. Clique no Play para iniciar.');
      });
    });
  }

  // ─── 5. Export / Captura de tempo ─────────────────────────────────────────
  function videoTimeToHHMMSS() {
    const curTime = player.currentTime();
    if (!playlistStartTime || isNaN(curTime)) return null;
    const [h, m, s] = playlistStartTime.split(':').map(Number);
    const baseSeconds = h * 3600 + m * 60 + s;
    const total = Math.floor(baseSeconds + curTime) % (24 * 3600);
    const hh = String(Math.floor(total / 3600)).padStart(2, '0');
    const mm = String(Math.floor((total % 3600) / 60)).padStart(2, '0');
    const ss = String(total % 60).padStart(2, '0');
    return `${hh}:${mm}:${ss}`;
  }

  setStartBtn.addEventListener('click', () => {
    const t = videoTimeToHHMMSS();
    if (t) { exportStart.value = t; flashBtn(setStartBtn); }
    else { exportMsg.style.color = '#f59e0b'; exportMsg.textContent = 'Reproduza um vídeo antes de capturar o tempo.'; }
  });

  setEndBtn.addEventListener('click', () => {
    const t = videoTimeToHHMMSS();
    if (t) { exportEnd.value = t; flashBtn(setEndBtn); }
    else { exportMsg.style.color = '#f59e0b'; exportMsg.textContent = 'Reproduza um vídeo antes de capturar o tempo.'; }
  });

  function flashBtn(btn) {
    btn.classList.add('time-now-btn--flash');
    setTimeout(() => btn.classList.remove('time-now-btn--flash'), 600);
  }

  exportForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const camera = cameraSelect.value;
    const date = dateSelect.value;
    const start_time = normaliseTime(exportStart.value);
    const end_time   = normaliseTime(exportEnd.value);

    if (!camera || !date) {
      exportMsg.style.color = '#ef4444';
      exportMsg.textContent = 'Selecione uma câmera e data válidas.';
      return;
    }
    if (!start_time || !end_time) {
      exportMsg.style.color = '#ef4444';
      exportMsg.textContent = 'Preencha os horários de início e fim.';
      return;
    }

    exportBtn.disabled = true;
    exportBtnLabel.textContent = 'Processando...';
    exportProgress.classList.remove('hidden');
    exportMsg.textContent = '';

    const downloadUrl = `/api/export_clip?camera=${encodeURIComponent(camera)}&date=${encodeURIComponent(date)}&start_time=${encodeURIComponent(start_time)}&end_time=${encodeURIComponent(end_time)}`;

    try {
      const res = await fetch(downloadUrl);
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Erro desconhecido.' }));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const blob = await res.blob();
      const objUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      const filename = `clip_${camera}_${date}_${start_time.replace(/:/g,'-')}_a_${end_time.replace(/:/g,'-')}.mp4`;
      a.href = objUrl; a.download = filename;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(objUrl), 60000);
      exportMsg.style.color = '#10b981';
      exportMsg.textContent = `✅ Download iniciado: ${filename}`;
    } catch (err) {
      console.error(err);
      exportMsg.style.color = '#ef4444';
      exportMsg.textContent = `❌ ${err.message}`;
    } finally {
      exportBtn.disabled = false;
      exportBtnLabel.textContent = 'Baixar MP4';
      exportProgress.classList.add('hidden');
    }
  });

  // ─── Utils ─────────────────────────────────────────────────────────────────
  function normaliseTime(val) {
    if (!val) return null;
    const parts = val.split(':');
    if (parts.length === 2) return `${parts[0]}:${parts[1]}:00`;
    if (parts.length === 3) return val;
    return null;
  }

  function showPlaceholder(visible, message = '', showSpinner = false) {
    if (visible) {
      playerPlaceholder.classList.remove('hidden');
      loadingSpinner.style.display = showSpinner ? 'block' : 'none';
      if (message) placeholderText.textContent = message;
    } else {
      playerPlaceholder.classList.add('hidden');
    }
  }

  // ─── Controle fino com setas do teclado ───────────────────────────────────
  // Seta direita/esquerda: avança/retrocede 30s (aumenta o passo com Shift)
  // Seta cima/baixo: avança/retrocede 10s (mais fino)
  // Evita conflito com os atalhos nativos do Video.js quando o player está focado.
  const ARROW_JUMP = 30;
  const ARROW_FINE_JUMP = 10;

  document.addEventListener('keydown', (e) => {
    if (!currentPlaylistUrl) return;
    // Se estiver digitando num campo de formulário, não interfere
    const tag = (e.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select' || e.target.isContentEditable) return;

    let delta = null;
    switch (e.key) {
      case 'ArrowRight': delta = e.shiftKey ? ARROW_JUMP * 6 : ARROW_JUMP; break;
      case 'ArrowLeft':  delta = e.shiftKey ? -ARROW_JUMP * 6 : -ARROW_JUMP; break;
      case 'ArrowUp':    delta = ARROW_FINE_JUMP; break;
      case 'ArrowDown':  delta = -ARROW_FINE_JUMP; break;
      default: return;
    }

    e.preventDefault();
    const cur = player.currentTime();
    if (isNaN(cur) || !isFinite(cur)) return;
    const target = Math.max(0, cur + delta);
    // Ajusta currentTime — o handler de 'seeking' cuida do recarregamento
    player.currentTime(target);
  });

  function showStatus(msg) {
    statusMessage.textContent = msg;
  }
});

