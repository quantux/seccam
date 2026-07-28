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
