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
COPY --from=builder /app/config /app/config

RUN mkdir -p /app/logs

ENV SYMBOL=BTCUSDT
ENV TIMEFRAME_MIN=1
ENV MONITOR_EXCHANGE=binance_linear
ENV MONITOR_PROFILE=mtf_futures_paper

# Fail fast if critical Railway env vars are missing instead of silently running disabled.
CMD ["sh", "-c", "\
  if [ -z \"$SUPABASE_URL\" ]; then echo '[ERROR] SUPABASE_URL not set — aborting'; exit 1; fi; \
  if [ -z \"$SUPABASE_KEY\" ]; then echo '[ERROR] SUPABASE_KEY not set — aborting'; exit 1; fi; \
  exec monitor"]
