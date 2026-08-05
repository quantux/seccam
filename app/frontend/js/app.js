document.addEventListener('DOMContentLoaded', () => {
  const cameraSelect = document.getElementById('cameraSelect');
  const dateSelect = document.getElementById('dateSelect');
  const videoPlayer = document.getElementById('videoPlayer');
  const playerPlaceholder = document.getElementById('playerPlaceholder');
  const placeholderText = document.getElementById('placeholderText');
  const loadingSpinner = document.getElementById('loadingSpinner');
  const statusMessage = document.getElementById('statusMessage');
  const cameraStatusText = document.getElementById('cameraStatusText');

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
  // Stores the start time of the currently loaded day's first segment (for relative-time offset)
  let playlistStartTime = null; // "HH:MM:SS" string of day start

  // Estado inicial: sem spinner e com mensagem clara
  showPlaceholder(true, 'Selecione uma câmera e uma data para assistir', false);

  // 1. Carrega Lista de Câmeras ao iniciar
  fetchCameras();

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
        const option = document.createElement('option');
        option.value = cam.name;
        option.textContent = cam.name;
        cameraSelect.appendChild(option);
      });

      showStatus(`Encontradas ${cameras.length} câmera(s).`);
    } catch (err) {
      console.error(err);
      showStatus('Erro ao conectar ao servidor de câmeras.');
    }
  }

  // 2. Quando seleciona uma câmera, busca as datas disponíveis
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
        showStatus('Nenhuma gravação encontrada ainda para esta câmera.');
        showPlaceholder(true, `A gravação para ${selectedCamera} iniciou. Aguarde até o primeiro segmento .ts ser finalizado.`, false);
        return;
      }

      // Habilita campo de data e define o dia mais recente por padrão
      dateSelect.disabled = false;
      exportBtn.disabled = false;
      dateSelect.min = data.dates[data.dates.length - 1];
      dateSelect.max = data.dates[0];
      dateSelect.value = data.dates[0]; // Seleciona a data mais recente

      showStatus(`Encontradas gravações para ${data.dates.length} dia(s).`);
      loadPlaylist();
    } catch (err) {
      console.error(err);
      showStatus('Erro ao buscar datas de gravação.');
    }
  });

  // 4. Ao alterar a data
  dateSelect.addEventListener('change', () => {
    if (dateSelect.value && cameraSelect.value) {
      exportBtn.disabled = false;
      loadPlaylist();
    }
  });

  // ---- Capture current video time helpers ----
  function videoTimeToHHMMSS() {
    if (!playlistStartTime || isNaN(videoPlayer.currentTime)) return null;
    // playlistStartTime is the first segment's time (e.g. "14:00:00")
    const [h, m, s] = playlistStartTime.split(':').map(Number);
    const baseSeconds = h * 3600 + m * 60 + s;
    // O input HTML de horário aceita somente 00:00:00–23:59:59.
    const total = Math.floor(baseSeconds + videoPlayer.currentTime) % (24 * 60 * 60);
    const hh = String(Math.floor(total / 3600)).padStart(2, '0');
    const mm = String(Math.floor((total % 3600) / 60)).padStart(2, '0');
    const ss = String(total % 60).padStart(2, '0');
    return `${hh}:${mm}:${ss}`;
  }

  setStartBtn.addEventListener('click', () => {
    const t = videoTimeToHHMMSS();
    if (t) {
      exportStart.value = t; // HH:MM:SS — time input accepts this
      flashBtn(setStartBtn);
    } else {
      exportMsg.style.color = '#f59e0b';
      exportMsg.textContent = 'Reproduza um vídeo antes de capturar o tempo.';
    }
  });

  setEndBtn.addEventListener('click', () => {
    const t = videoTimeToHHMMSS();
    if (t) {
      exportEnd.value = t;
      flashBtn(setEndBtn);
    } else {
      exportMsg.style.color = '#f59e0b';
      exportMsg.textContent = 'Reproduza um vídeo antes de capturar o tempo.';
    }
  });

  function flashBtn(btn) {
    btn.classList.add('time-now-btn--flash');
    setTimeout(() => btn.classList.remove('time-now-btn--flash'), 600);
  }

  // ---- Export / Download ----
  // 5. Exportar Trecho em MP4
  exportForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const camera = cameraSelect.value;
    const date = dateSelect.value;
    // time input gives "HH:MM" or "HH:MM:SS" — normalise to HH:MM:SS
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

    // Show progress state
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

      // Trigger browser download via blob
      const blob = await res.blob();
      const objUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      const filename = `clip_${camera}_${date}_${start_time.replace(/:/g,'-')}_a_${end_time.replace(/:/g,'-')}.mp4`;
      a.href = objUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
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

  function normaliseTime(val) {
    if (!val) return null;
    // "HH:MM" → "HH:MM:00", "HH:MM:SS" → pass through
    const parts = val.split(':');
    if (parts.length === 2) return `${parts[0]}:${parts[1]}:00`;
    if (parts.length === 3) return val;
    return null;
  }

  function loadPlaylist() {
    const camera = cameraSelect.value;
    const date = dateSelect.value;

    if (!camera || !date) {
      showStatus('Selecione uma câmera e uma data válida.');
      return;
    }

    const playlistUrl = `/api/playlist.m3u8?camera=${encodeURIComponent(camera)}&date=${encodeURIComponent(date)}`;

    showPlaceholder(true, 'Carregando vídeo do dia...', true);
    playlistStartTime = null;

    // Destrói instância HLS prévia se existir
    if (currentHls) {
      currentHls.destroy();
      currentHls = null;
    }

    if (Hls.isSupported()) {
      const hls = new Hls({
        debug: false,
        enableWorker: true,
        lowLatencyMode: false,

        // Configurações de buffer otimizadas para gravações de 10h+
        backBufferLength: 120,          // Mantém 2 min de histórico para retroceder rápido
        maxBufferLength: 120,           // Buffer de até 2 min à frente
        maxMaxBufferLength: 600,        // Teto máximo de buffer (10 minutos)
        maxBufferSize: 60 * 1024 * 1024, // Limite de 60 MB de memória

        // Ajustes para busca (seek) fluida e recuperação automática de buracos de tempo
        maxBufferHole: 0.8,             // Salta pequenas lacunas de timestamps (até 0.8s) ao buscar
        highBufferWatchdogPeriod: 2,    // Monitora estagnação do player a cada 2s
        nudgeMaxRetry: 10,              // Tenta empurrar o cursor além de buracos até 10 vezes
        nudgeOffset: 0.2,               // Passo do empurrão (0.2s) se ficar preso em lacuna

        // Tolerância de rede
        fragLoadingTimeOut: 30000,
        fragLoadingMaxRetry: 6,
        fragLoadingRetryDelay: 1000,
        manifestLoadingTimeOut: 30000,
        manifestLoadingMaxRetry: 6,
      });

      currentHls = hls;
      hls.loadSource(playlistUrl);
      hls.attachMedia(videoPlayer);

      const extractStartTime = (details) => {
        try {
          const firstFrag = details?.fragments?.[0];
          if (firstFrag) {
            const urlParams = new URLSearchParams(new URL(firstFrag.url, location.href).search);
            const file = urlParams.get('file') || '';
            const match = file.match(/(\d{2})-(\d{2})-(\d{2})\.ts$/);
            if (match) {
              playlistStartTime = `${match[1]}:${match[2]}:${match[3]}`;
            }
          }
        } catch (_) {}
      };

      hls.on(Hls.Events.LEVEL_LOADED, (event, data) => {
        extractStartTime(data.details);
      });

      hls.on(Hls.Events.MANIFEST_PARSED, (event, data) => {
        showPlaceholder(false);
        showStatus('Vídeo pronto para reprodução.');
        videoPlayer.play().catch(e => console.log('Autoplay prevenido:', e));
      });

      let mediaErrorCount = 0;
      hls.on(Hls.Events.ERROR, (event, data) => {
        if (data.fatal) {
          console.error('Erro HLS Fatal:', data);
          switch (data.type) {
            case Hls.ErrorTypes.NETWORK_ERROR:
              showStatus('Erro de rede ao carregar playlist. Tentando reconectar...');
              hls.startLoad();
              break;
            case Hls.ErrorTypes.MEDIA_ERROR:
              mediaErrorCount++;
              if (mediaErrorCount <= 3) {
                showStatus('Recuperando erro de decodificação de mídia...');
                hls.recoverMediaError();
              } else {
                showStatus('Recarregando codecs para estabilizar mídia...');
                hls.swapAudioCodec();
                hls.recoverMediaError();
                mediaErrorCount = 0;
              }
              break;
            default:
              showPlaceholder(true, 'Não foi possível carregar o vídeo.', false);
              hls.destroy();
              break;
          }
        }
      });

      // Monitora estagnações ao avançar/retroceder na barra de progresso
      let stallTimeout = null;
      const clearStallWatchdog = () => {
        if (stallTimeout) {
          clearTimeout(stallTimeout);
          stallTimeout = null;
        }
      };

      videoPlayer.addEventListener('waiting', () => {
        clearStallWatchdog();
        stallTimeout = setTimeout(() => {
          if (!videoPlayer.paused && videoPlayer.readyState < 3 && currentHls) {
            console.warn('Player travado após seek. Ajustando posição para destravar...');
            videoPlayer.currentTime += 0.2;
          }
        }, 2500);
      });

      videoPlayer.addEventListener('playing', clearStallWatchdog);
      videoPlayer.addEventListener('seeked', clearStallWatchdog);

    } else if (videoPlayer.canPlayType('application/vnd.apple.mpegurl')) {
      // Suporte nativo ao HLS (Safari iOS/macOS)
      videoPlayer.src = playlistUrl;
      videoPlayer.addEventListener('loadedmetadata', () => {
        showPlaceholder(false);
        videoPlayer.play();
      });
    } else {
      showPlaceholder(true, 'Seu navegador não possui suporte a reprodução HLS.', false);
    }
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
