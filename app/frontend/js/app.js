document.addEventListener('DOMContentLoaded', () => {
  const cameraSelect = document.getElementById('cameraSelect');
  const dateSelect = document.getElementById('dateSelect');
  const videoPlayer = document.getElementById('videoPlayer');
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

  let currentHls = null;
  let currentPlaylistUrl = null;
  let playlistStartTime = null; // "HH:MM:SS" do primeiro segmento do dia

  // Flag para evitar loop: quando recriamos o HLS internamente, não queremos
  // que o evento 'seeking' do video dispare outro reload.
  let isSeeking = false;

  // Configuração base do HLS.js — compartilhada entre loadPlaylist e reloadAtPosition
  function buildHlsConfig(startPosition = -1) {
    return {
      debug: false,
      enableWorker: true,
      lowLatencyMode: false,
      progressive: false,

      // startPosition: -1 = início; ≥ 0 = posição em segundos
      startPosition,

      // Buffer menor para seek mais rápido em rede remota
      backBufferLength: 30,
      maxBufferLength: 30,
      maxMaxBufferLength: 120,
      maxBufferSize: 30 * 1024 * 1024,

      // Tolerância a lacunas de timestamp nos segmentos
      maxBufferHole: 2.0,
      highBufferWatchdogPeriod: 4,
      nudgeMaxRetry: 10,
      nudgeOffset: 0.3,

      // Timeouts generosos para rede remota (Raspberry Pi)
      fragLoadingTimeOut: 60000,
      fragLoadingMaxRetry: 5,
      fragLoadingRetryDelay: 1500,
      manifestLoadingTimeOut: 30000,
      manifestLoadingMaxRetry: 5,
      levelLoadingTimeOut: 30000,
      levelLoadingMaxRetry: 5,
    };
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

  // ─── 4. Seek confiável: intercepta o evento do browser e recria o HLS ─────
  // O HLS.js tem comportamento instável ao fazer seek em VOD longo com muitas
  // descontinuidades: o vídeo pula para posições erradas ou trava infinitamente.
  // A solução é destruir o HLS.js e recriar com startPosition = tempo desejado.
  let seekDebounce = null;

  videoPlayer.addEventListener('seeking', () => {
    // Ignora eventos de seeking disparados pelo próprio reload interno
    if (isSeeking || !currentPlaylistUrl) return;

    clearTimeout(seekDebounce);
    const targetTime = videoPlayer.currentTime;

    // Pequeno debounce para não disparar em cada pixel do scrubbing
    seekDebounce = setTimeout(() => {
      performSeek(targetTime);
    }, 400);
  });

  function performSeek(targetSeconds) {
    if (!currentPlaylistUrl || isSeeking) return;

    console.log(`[seek] Recarregando em ${targetSeconds.toFixed(1)}s`);
    isSeeking = true;

    const wasPlaying = !videoPlayer.paused;

    destroyHls();

    const hls = createHlsInstance(targetSeconds);

    hls.on(Hls.Events.MANIFEST_PARSED, () => {
      isSeeking = false;
      showStatus(`Buscando ${formatSeconds(targetSeconds)}...`);
      if (wasPlaying) {
        videoPlayer.play().catch(() => {});
      }
    });

    hls.on(Hls.Events.FRAG_CHANGED, (event, data) => {
      // Assim que o primeiro fragmento carregou, atualiza status
      const t = videoPlayer.currentTime;
      showStatus(`Reproduzindo a partir de ${formatSeconds(t)}`);
    });

    setupHlsErrorHandling(hls);
  }

  // ─── 5. Carrega playlist do zero (câmera/data nova) ───────────────────────
  function loadPlaylist() {
    const camera = cameraSelect.value;
    const date = dateSelect.value;
    if (!camera || !date) return;

    currentPlaylistUrl = `/api/playlist.m3u8?camera=${encodeURIComponent(camera)}&date=${encodeURIComponent(date)}`;
    playlistStartTime = null;
    isSeeking = false;

    showPlaceholder(true, 'Carregando vídeo...', true);
    destroyHls();

    const hls = createHlsInstance(-1); // -1 = começa do início

    hls.on(Hls.Events.MANIFEST_PARSED, () => {
      showPlaceholder(false);
      showStatus('Vídeo pronto para reprodução.');
      videoPlayer.play().catch(e => console.log('Autoplay bloqueado:', e));
    });

    hls.on(Hls.Events.LEVEL_LOADED, (event, data) => {
      // Extrai o horário do primeiro segmento para cálculo de export
      try {
        const firstFrag = data.details?.fragments?.[0];
        if (firstFrag) {
          const params = new URLSearchParams(new URL(firstFrag.url, location.href).search);
          const file = params.get('file') || '';
          const m = file.match(/(\d{2})-(\d{2})-(\d{2})\.ts$/);
          if (m) playlistStartTime = `${m[1]}:${m[2]}:${m[3]}`;
        }
      } catch (_) {}
    });

    setupHlsErrorHandling(hls);
  }

  // ─── Helpers de HLS ────────────────────────────────────────────────────────
  function createHlsInstance(startPosition) {
    if (!Hls.isSupported()) {
      // Fallback nativo (Safari)
      videoPlayer.src = currentPlaylistUrl;
      return { on: () => {}, destroy: () => {} };
    }

    const hls = new Hls(buildHlsConfig(startPosition));
    currentHls = hls;
    hls.loadSource(currentPlaylistUrl);
    hls.attachMedia(videoPlayer);
    return hls;
  }

  function destroyHls() {
    if (currentHls) {
      currentHls.destroy();
      currentHls = null;
    }
  }

  let mediaErrorCount = 0;
  function setupHlsErrorHandling(hls) {
    hls.on(Hls.Events.ERROR, (event, data) => {
      if (!data.fatal) return;

      console.error('[HLS error]', data.type, data.details);
      switch (data.type) {
        case Hls.ErrorTypes.NETWORK_ERROR:
          showStatus('Erro de rede — tentando reconectar...');
          setTimeout(() => hls.startLoad(), 2000);
          break;
        case Hls.ErrorTypes.MEDIA_ERROR:
          mediaErrorCount++;
          if (mediaErrorCount <= 3) {
            showStatus('Recuperando erro de decodificação...');
            hls.recoverMediaError();
          } else {
            showStatus('Reiniciando player...');
            hls.swapAudioCodec();
            hls.recoverMediaError();
            mediaErrorCount = 0;
          }
          break;
        default:
          isSeeking = false;
          showPlaceholder(true, 'Não foi possível carregar o vídeo. Tente novamente.', false);
          hls.destroy();
          break;
      }
    });
  }

  // ─── 6. Export / Captura de tempo ─────────────────────────────────────────
  function videoTimeToHHMMSS() {
    if (!playlistStartTime || isNaN(videoPlayer.currentTime)) return null;
    const [h, m, s] = playlistStartTime.split(':').map(Number);
    const baseSeconds = h * 3600 + m * 60 + s;
    const total = Math.floor(baseSeconds + videoPlayer.currentTime) % (24 * 3600);
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

  function formatSeconds(s) {
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = Math.floor(s % 60);
    return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
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

  function showStatus(msg) {
    statusMessage.textContent = msg;
  }
});
