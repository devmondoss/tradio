# Guía: Correr el monitor en local (Windows)

---

## Prerequisitos

### 1. Rust con toolchain GNU (MinGW-w64)

El monitor requiere el toolchain `x86_64-pc-windows-gnu` — el toolchain MSVC falla con la dependencia de criptografía.

```powershell
# Instalar MinGW-w64 via WinGet
winget install BrechtSanders.WinLibs.POSIX.LLVM

# Añadir toolchain GNU a Rust
rustup target add x86_64-pc-windows-gnu
rustup toolchain install stable-x86_64-pc-windows-gnu
```

### 2. Linker MinGW en PATH

El problema más común: `cargo build` usa el `link.exe` de Git (`C:\Program Files\Git\usr\bin`) en vez del linker de MinGW. Resultado: `kernel32.dll_imports.lib not found`.

**Solución**: prepend del bin de MinGW al PATH en la sesión de PowerShell:

```powershell
$mingwBin = (Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Filter "BrechtSanders*" |
             Sort-Object LastWriteTime -Descending |
             Select-Object -First 1).FullName + "\mingw64\bin"
$env:PATH = "$mingwBin;$env:PATH"
```

O hardcodear la ruta si ya se conoce:
```powershell
$env:PATH = "C:\Users\{user}\AppData\Local\Microsoft\WinGet\Packages\BrechtSanders.WinLibs.POSIX.LLVM_...\mingw64\bin;$env:PATH"
```

---

## Variables de entorno

Crear `.env` en la raíz del proyecto (nunca commiteado a git):

```
SUPABASE_URL=https://{project-ref}.supabase.co
SUPABASE_KEY={service_role_key}
SYMBOL=BTCUSDT
TIMEFRAME_MIN=5

# Paper trading (opcional — usa defaults si no se definen)
PAPER_INITIAL_CAPITAL=3000.0
PAPER_LEVERAGE=1.0
PAPER_MAX_POSITIONS=1
PAPER_RISK_PCT=0.01
PAPER_SLIPPAGE_BPS=1.0
PAPER_TAKER_FEE=0.0004
PAPER_FUNDING_RATE=0.0001
```

El monitor Rust carga las variables de entorno al arrancar desde las variables del shell (Railway) o desde el `.env` vía `dotenv` si está disponible.

---

## Compilar y correr

### Opción A: Script `run.bat`

Si existe `run.bat` en la raíz, simplemente:
```
run.bat
```

### Opción B: PowerShell manual

```powershell
# 1. Prepend MinGW al PATH
$mingwBin = ... (ver arriba)
$env:PATH = "$mingwBin;$env:PATH"

# 2. Cargar variables del .env (si no se cargaron automáticamente)
Get-Content .env | ForEach-Object {
    if ($_ -match '^(\w+)=(.+)$') { [System.Environment]::SetEnvironmentVariable($matches[1], $matches[2]) }
}

# 3. Compilar (primera vez ~3 min, después ~10s en debug)
cargo build -p monitor --target x86_64-pc-windows-gnu

# 4. Correr con backtrace habilitado
$env:RUST_BACKTRACE = "1"
.\target\x86_64-pc-windows-gnu\debug\monitor.exe
```

### Compilar en release (para comparar rendimiento)

```powershell
cargo build -p monitor --target x86_64-pc-windows-gnu --release
.\target\x86_64-pc-windows-gnu\release\monitor.exe
```

---

## Outputs esperados al arrancar

**stderr** (diagnóstico — se ve en consola):
```
[bar] ts=1234567890000 close=95420.50 regime=TrendUp ...
[config] Loaded calibrated params for regime 'TrendUp': min_score=0.68 ...
paper: estado restaurado — balance=3000.00 open=0 closed=12
```

**stdout** (señales — Railway captura esto en sus logs):
```json
{"event":"signal","data":{"strategy":"LvnLiquidityVacuumBreakout","side":"Long",...}}
{"event":"trade_closed","data":{"close_reason":"TARGET_HIT","r_multiple":1.8,...}}
```

**Archivos locales** (en `./logs/` o `data/logs/` según dónde corra):
- `strategy_signals.jsonl`
- `strategy_rejected.jsonl`
- `strategy_blocked.jsonl`
- `strategy_outcomes.jsonl`
- `paper_trades.jsonl`
- `paper_account_state.json`

---

## Tests unitarios

```powershell
# Todos los tests del workspace
cargo test --target x86_64-pc-windows-gnu

# Solo tests de la capa de strategy
cargo test -p data --target x86_64-pc-windows-gnu

# Un test específico
cargo test -p data --target x86_64-pc-windows-gnu full_accounting_long_target_hit
```

---

## Problemas comunes

| Error | Causa | Solución |
|-------|-------|----------|
| `kernel32.dll_imports.lib not found` | Git's link.exe tomando precedencia sobre MinGW | Prepend MinGW bin al PATH |
| `error[E0432]: unresolved import serde` | serde no en Cargo.toml del crate que lo necesita | Añadir `serde.workspace = true` al Cargo.toml del crate |
| `CryptoProvider` panic al arrancar | rustls requiere `install_default()` | Llamar `rustls::crypto::ring::default_provider().install_default()` en main() |
| WebSocket no conecta | Binance API bloqueada en la región | Usar VPN o correr directamente en Railway (asia-southeast1) |
| `paper_account_state.json corrupto` | Crash durante write | El monitor arranca de cero automáticamente — sin intervención manual |
