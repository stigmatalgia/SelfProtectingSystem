FROM quorumengineering/quorum

RUN apk add --update bash nodejs npm nano perf linux-tools-doc

WORKDIR /home/qbft

RUN npm install quorum-genesis-tool solc@0.8.19 fs web3 @openzeppelin/contracts


ENTRYPOINT /bin/bash