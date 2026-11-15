# KiCad 10 reads KiCad 7, 8, 9 and 10 projects. ngspice runs the analog fragments.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PCBTESTER_DATA=/data

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates software-properties-common \
    && add-apt-repository -y ppa:kicad/kicad-10.0-releases \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        kicad \
        ngspice \
        python3 \
        python3-pip \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY pcbtester ./pcbtester
RUN pip3 install --no-cache-dir --break-system-packages .

EXPOSE 8080
VOLUME ["/data"]
CMD ["pcbtester", "serve", "--host", "0.0.0.0", "--port", "8080"]
