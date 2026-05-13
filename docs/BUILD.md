# Build Setup

## Por qué no usar `cargo run` directamente

Git incluye su propio `link.exe` en el PATH. Si se usa el toolchain MSVC de Rust, el sistema encuentra el `link.exe` de Git antes que el de MSVC y el build falla silenciosamente. La solución es usar el toolchain GNU con MinGW-w64.

## Toolchain

```
stable-x86_64-pc-windows-gnu
```

Configurado en el directorio del proyecto con:
```
rustup override set stable-x86_64-pc-windows-gnu
```

## MinGW-w64

**Ruta:**
```
C:\Users\inkam\AppData\Local\Microsoft\WinGet\Packages\BrechtSanders.WinLibs.POSIX.UCRT_Microsoft.Winget.Source_8wekyb3d8bbwe\mingw64\bin
```

**Instalación:**
```
winget install BrechtSanders.WinLibs.POSIX.UCRT
```

## Cómo ejecutar

```bat
./run.bat
```

Nunca usar `cargo run` solo. El script `run.bat` en la raíz del proyecto hace:

```bat
@echo off
set PATH=C:\Users\inkam\AppData\...\mingw64\bin;%PATH%
set RUST_BACKTRACE=1
cargo run
```

`RUST_BACKTRACE=1` está activo para capturar stacktraces de panics en desarrollo.
