FROM ubuntu:22.04 AS builder

RUN apt-get update && apt-get install -y curl tar tzdata
RUN curl -L https://github.com/evmos/evmos/releases/download/v20.0.0/evmos_20.0.0_Linux_amd64.tar.gz -o /tmp/evmos.tar.gz
RUN mkdir -p /tmp/evmos && tar -xzf /tmp/evmos.tar.gz -C /tmp/evmos/
RUN ls -la /tmp/evmos/ && cp /tmp/evmos/bin/evmosd /evmosd || cp /tmp/evmos/evmosd /evmosd || find /tmp/evmos -name evmosd -exec cp {} /evmosd \;

FROM kathara/base

COPY --from=builder /evmosd /usr/local/bin/evmosd
RUN chmod +x /usr/local/bin/evmosd

# Verify it works
RUN evmosd version

RUN apt-get update && apt-get install -y nodejs npm nano tcpdump jq && rm -rf /var/lib/apt/lists/*

WORKDIR /home/cometbft

RUN npm install solc@0.8.19 fs web3 @openzeppelin/contracts

ENTRYPOINT ["/bin/bash"]
