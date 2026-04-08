FROM kathara/base

RUN apt-get update && apt-get install -y \
    openssh-client \
    python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /home/actuator

ENTRYPOINT /bin/bash
