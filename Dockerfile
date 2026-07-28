FROM python:3.11-bullseye

# Define diretório de trabalho
WORKDIR /app

# Fuso horário
ENV TZ="America/Sao_Paulo"

# Instala dependências do sistema
RUN apt-get update && apt-get install -y \
    ca-certificates \
    ffmpeg \
    iputils-ping \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Configura fuso horário
RUN ln -fs /usr/share/zoneinfo/America/Sao_Paulo /etc/localtime && \
    dpkg-reconfigure -f noninteractive tzdata

# Copia requirements e instala dependências Python
COPY app/requirements.txt .
RUN pip install --upgrade pip
RUN pip install -r requirements.txt --break-system-packages

# Copia todo o código da aplicação
COPY app/ .

# Permite manter logs em volume
VOLUME ["/videos", "/configs"]

EXPOSE 8000

# Comando de inicialização
ENTRYPOINT ["python3", "main.py"]

