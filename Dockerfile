FROM rust:1.95-slim AS builder

# Bypass rust-toolchain.toml channel sync — uses the toolchain already in the image.
# Without this, rustup tries to download channel-rust-1.95.0.toml from
# static.rust-lang.org during `cargo build`, which fails on Railway's build servers.
ENV RUSTUP_TOOLCHAIN=1.95.0

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
