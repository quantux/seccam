# Security Camera

Sistema de gravação e monitoramento de câmeras RTSP com server web para visualização ao vivo e playback, além de upload automático para o YouTube.

## Funcionalidades

- Gravação simultânea de múltiplas câmeras RTSP via ffmpeg
- Player HLS ao vivo no navegador (http://localhost:8000)
- Playback com timeline para navegar por data/hora
- Upload automático para YouTube em horários configuráveis
- Limpeza automática de vídeos antigos (retenção configurável)
- API REST (FastAPI)

## Estrutura

```
.
├── app/                      # Código fonte
│   ├── api/                  # API REST (FastAPI)
│   ├── frontend/             # Interface web
│   ├── hls/                  # Gerenciamento de segmentos HLS
│   ├── recorder/             # Gravação via ffmpeg
│   ├── storage/              # Gerenciamento de arquivos
│   ├── detector/             # Detecção de pessoas (NanoDet/ONNX) + ntfy
│   ├── uploader/             # Upload para YouTube (Google OAuth2)
│   ├── cleaner.py            # Limpeza de vídeos antigos
│   ├── config.py             # Configurações
│   └── main.py               # Entrypoint
├── configs/                  # Configurações (volume montado)
│   ├── cameras.json          # Câmeras RTSP
│   ├── secrets/              # Credenciais Google OAuth2
│   └── logs/                 # Logs
├── videos/                   # Gravações (volume montado)
├── docker-compose.yml
├── Dockerfile
└── .gitignore
```

## Quick start

```bash
# 1. Configure as câmeras
cp configs/cameras.example.json configs/cameras.json
# Edite com as URLs RTSP das suas câmeras

# 2. (Opcional) Configure upload para YouTube
# Coloque client_secrets.json em configs/secrets/
# E execute o fluxo OAuth2:
# docker compose run --rm security_camera python uploader/oauth2_flow.py /configs/secrets/client_secrets.json

# 3. Build e execute
docker compose up -d
```

Acesse `http://localhost:8000` para ver as câmeras ao vivo.

## Configuração

### Câmeras (`configs/cameras.json`)

```json
{
  "cameras": [
    {
      "name": "camera_1",
      "rtsp_url": "rtsp://usuario:senha@192.168.1.100:8554/camera_1"
    }
  ]
}
```

### Variáveis de ambiente

| Variável       | Padrão | Descrição                            |
|----------------|--------|--------------------------------------|
| `SEGMENT_TIME` | `60`   | Duração de cada segmento (segundos)  |
| `DAYS_TO_KEEP` | `3`    | Dias de retenção dos vídeos          |
| `RETRY_DELAY`  | `10`   | Delay antes de reiniciar gravação    |
| `ENABLE_DETECTOR` | `1` | Liga/desliga a detecção de pessoas   |
| `SECCAM_HWACCEL` | `auto` | Decoder H.264: `auto`, `off` ou `h264_v4l2m2m` |

### Volumes

| Caminho      | Descrição                        |
|--------------|----------------------------------|
| `./configs`  | Configurações, credenciais, logs |
| `./videos`   | Gravações de vídeo               |

## Upload YouTube

O upload automático ocorre duas vezes ao dia (07:00 e 19:00 por padrão, configurável em `app/config.py`).

Para configurar:
1. Crie um projeto no [Google Cloud Console](https://console.cloud.google.com/)
2. Habilite a API YouTube Data v3
3. Crie credenciais OAuth 2.0 e baixe como `client_secrets.json`
4. Coloque o arquivo em `configs/secrets/client_secrets.json`
5. Execute o fluxo de autenticação (veja Quick start passo 2)

## Detecção de pessoas (roi + ntfy)

Detecta uma pessoa que **permaneça** na área das portas e envia um snapshot por
ntfy. Se a pessoa apenas passar, nada é enviado. Uma notificação por evento
(só rearma quando a área fica livre).

- Modelo: NanoDet (ONNX, COCO) via OpenCV DNN — leve, roda em CPU.
- Fonte: **stream2** das câmeras, servida pelo mediamtx (`camera_3_sub`, `camera_4_sub`).
- Fluxo: `frame -> ROI -> gate de movimento -> modelo (person) -> dwell >= N s -> snapshot + ntfy`.
- O movimento só serve para acordar a inferência; a presença de uma pessoa
  **parada** é sustentada pelo modelo.
- No Raspberry Pi o decode H.264 usa hardware (`h264_v4l2m2m`); em x86 cai para software.

### Configuração pela interface

Acesse `http://<host>:8000/detection.html`:

1. Adicione as câmeras com a fonte RTSP do stream2 (ex.: `rtsp://192.168.1.2:8554/camera_3_sub`).
2. Clique em **Carregar frame** e **arraste sobre a imagem** para marcar a área da porta.
3. Preencha o **tópico ntfy** (invente um nome difícil) e clique em **Enviar notificação de teste**.
4. **Salvar configuração** e reinicie o container para aplicar às threads.

Assine o mesmo tópico no app ntfy (Android/iOS/F-Droid) e pronto.

### Arquivo de configuração

Gerado em `configs/detection.json` (veja `detection.example.json`). Principais campos:

| Campo | Padrão | Descrição |
|-------|--------|-----------|
| `dwell_seconds` | `5` | Tempo mínimo parado na ROI para alertar |
| `absence_tolerance_seconds` | `2` | Quanto pode sumir do detector antes de considerar que saiu |
| `probability_threshold` | `0.5` | Confiança mínima do modelo |
| `poll_fps` | `5` | FPS de análise do stream |
| `motion_ratio` | `0.01` | Fração de pixels alterados para considerar movimento |
| `notify.topic` | — | Tópico ntfy (obrigatório para alertar) |

### Hardware acceleration (opcional, Pi)

```bash
# no Raspberry Pi
docker compose -f docker-compose.yml -f docker-compose.pi.yml up -d
```

O override expõe `/dev/video10` e `/dev/vchiq` e liga `SECCAM_HWACCEL=auto`.
Para forçar software, use `SECCAM_HWACCEL=off`.

### Build multi-arch (amd64 + arm64)

```bash
docker buildx build --platform linux/amd64,linux/arm64 -t <usuario>/seccam:latest --push .
```

### Desativar a detecção

`ENABLE_DETECTOR=0` no ambiente do container (ou remova as câmeras do `detection.json`).
