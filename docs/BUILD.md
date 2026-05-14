# Build Setup

## Por qué no funciona `cargo run` directamente

Git incluye su propio `link.exe` en el PATH. Si se usa el toolchain MSVC de Rust, el sistema encuentra el `link.exe` de Git antes que el de MSVC y el build falla silenciosamente con errores de dlltool. La solución es usar el toolchain GNU con MinGW-w64.

---

## Toolchain

```
stable-x86_64-pc-windows-gnu
```

Configurado en el directorio del proyecto con:
```bash
rustup override set stable-x86_64-pc-windows-gnu
```

Para verificar:
```bash
rustup show active-toolchain
# → stable-x86_64-pc-windows-gnu (override)
```

---

## MinGW-w64

**Instalación:**
```powershell
winget install BrechtSanders.WinLibs.POSIX.UCRT
```

**Ruta instalada:**
```
C:\Users\inkam\AppData\Local\Microsoft\WinGet\Packages\
  BrechtSanders.WinLibs.POSIX.UCRT_Microsoft.Winget.Source_8wekyb3d8bbwe\
  mingw64\bin
```

Esta ruta debe estar en el PATH antes de ejecutar cargo.

---

## Cómo ejecutar

```bat
./run.bat
```

**Contenido de `run.bat`:**
```bat
@echo off
set PATH=C:\Users\inkam\AppData\Local\Microsoft\WinGet\Packages\BrechtSanders.WinLibs.POSIX.UCRT_Microsoft.Winget.Source_8wekyb3d8bbwe\mingw64\bin;%PATH%
set RUST_BACKTRACE=1
cargo run
```

`RUST_BACKTRACE=1` está activo para capturar stacktraces completos de cualquier panic.

---

## Cómo compilar sin ejecutar (para verificar errores)

Desde PowerShell con el PATH de MinGW:
```powershell
$env:PATH = "C:\Users\inkam\AppData\Local\Microsoft\WinGet\Packages\BrechtSanders.WinLibs.POSIX.UCRT_Microsoft.Winget.Source_8wekyb3d8bbwe\mingw64\bin;" + $env:PATH
cargo check
```

O desde la terminal del IDE (que hereda el PATH de `run.bat`).

---

## Dependencias externas relevantes

| Crate | Uso |
|-------|-----|
| `iced` | Framework de UI (canvas, widgets) |
| `lyon_path` | Construcción de paths vectoriales para el canvas — sensible a coordenadas NaN |
| `enum_map` | Indexado de indicadores por variante del enum |
| `serde_json` | Serialización de logs JSONL |
| `dirs_next` | Localización de `%APPDATA%` para guardar logs |
| `chrono` / `uuid` | Timestamps e IDs de requests |

---

## Datos persistidos por la app

```
%APPDATA%\Roaming\flowsurface\
└── shadow_events\
    ├── strategy_signals.jsonl    — cada señal al momento de detección
    └── strategy_outcomes.jsonl   — cada señal cuando cierra (MFE/MAE/outcome)
```

En Windows: `%APPDATA%` = `C:\Users\<usuario>\AppData\Roaming\`

---

## Notas para Claude Code

Siempre usar `PowerShell` para `cargo check`/`build`, con el PATH de MinGW configurado primero. El toolchain ya está configurado via `rustup override` en el directorio del proyecto, pero MinGW no está en el PATH del sistema — solo en `run.bat`.

```powershell
$env:PATH = "C:\Users\inkam\AppData\Local\Microsoft\WinGet\Packages\BrechtSanders.WinLibs.POSIX.UCRT_Microsoft.Winget.Source_8wekyb3d8bbwe\mingw64\bin;" + $env:PATH
Set-Location "C:\Users\inkam\Documents\flow-surface\flowsurface"
cargo check
```
