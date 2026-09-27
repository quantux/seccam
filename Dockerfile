FROM python:3.11-slim-bookworm

# Define diretório de trabalho
WORKDIR /app

# Fuso horário
ENV TZ="America/Sao_Paulo"

# Dependências de sistema:
#  - ffmpeg: gravação e decodificação das câmeras
#  - libglib2.0-0 / libgomp1 / libatomic1: runtime do OpenCV (headless)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    ffmpeg \
    iputils-ping \
    tzdata \
    libglib2.0-0 \
    libgomp1 \
    libatomic1 \
    && ln -fs /usr/share/zoneinfo/America/Sao_Paulo /etc/localtime \
    && rm -rf /var/lib/apt/lists/*

# Copia requirements e instala dependências Python
COPY app/requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copia todo o código da aplicação
COPY app/ .

# Permite manter logs em volume
VOLUME ["/videos", "/configs"]

EXPOSE 8000

# Comando de inicialização
ENTRYPOINT ["python3", "main.py"]
