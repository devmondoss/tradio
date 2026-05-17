FROM rust:1.95-slim AS builder

WORKDIR /app
RUN apt-get update && apt-get install -y pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*

COPY . .
RUN cargo build --release -p monitor

FROM debian:trixie-slim
RUN apt-get update && apt-get install -y ca-certificates && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /app/target/release/monitor /usr/local/bin/monitor

RUN mkdir -p /app/logs

ENV SYMBOL=BTCUSDT
ENV TIMEFRAME_MIN=5

CMD ["monitor"]
