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

  // Configuração recomendada do HLS.js para VOD com segmentos gravados em bloco
  function buildHlsConfig() {
    return {
      debug: false,
      enableWorker: true,
      lowLatencyMode: false,
      progressive: false,

      // Gerenciamento de buffer
      backBufferLength: 60,
      maxBufferLength: 60,
      maxMaxBufferLength: 300,
      maxBufferSize: 60 * 1024 * 1024,

      // Tolerância a descontinuidades e lacunas de timestamp nos arquivos .ts
      maxBufferHole: 0.5,
      highBufferWatchdogPeriod: 3,
      nudgeMaxRetry: 5,
      nudgeOffset: 0.2,

      // Timeouts e retentativas para evitar travamento em carregamentos mais lentos
      fragLoadingTimeOut: 30000,
      fragLoadingMaxRetry: 4,
      fragLoadingRetryDelay: 1000,
      manifestLoadingTimeOut: 20000,
      manifestLoadingMaxRetry: 3,
      levelLoadingTimeOut: 20000,
      levelLoadingMaxRetry: 3,
    };
  }

  // ─── Estado inicial ────────────────────────────────────────────────────────
  showPlaceholder(true, 'Selecione uma câmera e uma data para assistir', false);
  fetchCameras();

  // Eventos de estado do HTML5 Video Element
  videoPlayer.addEventListener('playing', () => {
    showPlaceholder(false);
  });

  videoPlayer.addEventListener('waiting', () => {
    // Não re-exibe a cortina preta de placeholder, só atualiza texto de status
    showStatus('Carregando buffer...');
  });

  videoPlayer.addEventListener('error', (e) => {
    console.error('Erro no elemento de vídeo:', videoPlayer.error);
    showStatus('Erro ao carregar mídia.');
  });

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

  // ─── 4. Carrega a playlist HLS para a câmera e data selecionadas ───────────
  function loadPlaylist() {
    const camera = cameraSelect.value;
    const date = dateSelect.value;
    if (!camera || !date) return;

    currentPlaylistUrl = `/api/playlist.m3u8?camera=${encodeURIComponent(camera)}&date=${encodeURIComponent(date)}`;
    playlistStartTime = null;

    showPlaceholder(true, 'Carregando vídeo...', true);
    destroyHls();

    if (Hls.isSupported()) {
      const hls = new Hls(buildHlsConfig());
      currentHls = hls;
      hls.loadSource(currentPlaylistUrl);
      hls.attachMedia(videoPlayer);

      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        showPlaceholder(false);
        showStatus('Vídeo pronto para reprodução.');
        videoPlayer.play().catch(e => console.log('Autoplay não iniciado automaticamente:', e));
      });

      hls.on(Hls.Events.LEVEL_LOADED, (event, data) => {
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
    } else if (videoPlayer.canPlayType('application/vnd.apple.mpegurl')) {
      // Fallback nativo (Safari em iOS/macOS)
      videoPlayer.src = currentPlaylistUrl;
      videoPlayer.addEventListener('loadedmetadata', () => {
        showPlaceholder(false);
        showStatus('Vídeo pronto para reprodução.');
        videoPlayer.play().catch(e => console.log('Autoplay não iniciado automaticamente:', e));
      });
    } else {
      showPlaceholder(true, 'Seu navegador não suporta reprodução HLS.', false);
    }
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
          setTimeout(() => {
            if (currentHls) currentHls.startLoad();
          }, 2000);
          break;
        case Hls.ErrorTypes.MEDIA_ERROR:
          mediaErrorCount++;
          if (mediaErrorCount <= 3) {
            showStatus('Recuperando erro de mídia...');
            hls.recoverMediaError();
          } else {
            showStatus('Reiniciando decodificação de áudio/vídeo...');
            hls.swapAudioCodec();
            hls.recoverMediaError();
            mediaErrorCount = 0;
          }
          break;
        default:
          showPlaceholder(true, 'Não foi possível carregar o vídeo. Tente novamente.', false);
          destroyHls();
          break;
      }
    });
  }

  // ─── 5. Export / Captura de tempo ─────────────────────────────────────────
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

