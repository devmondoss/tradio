import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { spawn } from 'child_process'
import path from 'path'
import type { Plugin } from 'vite'

function pythonBacktest(): Plugin {
  return {
    name: 'python-backtest',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = req.url ?? ''
        if (!url.startsWith('/api/backtest')) return next()

        const qs     = url.includes('?') ? url.slice(url.indexOf('?') + 1) : ''
        const params = new URLSearchParams(qs)
        const days   = params.get('days') ?? '14'
        const isBe         = url.startsWith('/api/backtest/be')
        const isSweep      = url.startsWith('/api/backtest/sweep')
        const isAbsorption = url.startsWith('/api/backtest/absorption')
        const isMtfLongs   = url.startsWith('/api/backtest/mtf_longs')
        const isMtfShorts  = url.startsWith('/api/backtest/mtf_shorts')
        const isMtfCombined = url.startsWith('/api/backtest/mtf_combined')
        const isMtfLocalLongs = url.startsWith('/api/backtest/mtf_local_longs')
        const isMtfLocal   = url.startsWith('/api/backtest/mtf_local')
        const isLongs      = url.startsWith('/api/backtest/longs')
        const isShorts     = url.startsWith('/api/backtest/shorts')

        const pyCmd = process.platform === 'win32' ? 'python' : 'python3'

        // ── Liquidity (provisión de liquidez en niveles de volumen, POC) ─────
        const isLiquidityInfo = url.startsWith('/api/backtest/liquidity_info')
        const isLiquidity     = url.startsWith('/api/backtest/liquidity')
        if (isLiquidityInfo || isLiquidity) {
          const wsRoot = path.join(server.config.root, '..', '..')
          const script = path.join(wsRoot, 'backtest', 'liquidity_app_backtest.py')
          const a = isLiquidityInfo ? ['--info'] : ['--days', days, '--json']
          console.log(`[backtest/liquidity] ${a.join(' ')}`)
          const py = spawn(pyCmd, [script, ...a])
          let out = ''; let err = ''
          py.stdout.on('data', (d: Buffer) => { out += d.toString() })
          py.stderr.on('data', (d: Buffer) => { err += d.toString() })
          py.on('error', (e: Error) => {
            res.writeHead(500, { 'Content-Type': 'application/json' })
            res.end(JSON.stringify({ error: e.message }))
          })
          py.on('close', (code: number) => {
            if (code !== 0 || !out.trim()) {
              res.writeHead(500, { 'Content-Type': 'application/json' })
              res.end(JSON.stringify({ error: err.trim() || `exit ${code}` }))
              return
            }
            res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
            res.end(out.trim())
          })
          return
        }

        // ── MTF Local Info: metadatos del parquet (días disponibles) ─────────
        const isMtfLocalInfo = url.startsWith('/api/backtest/mtf_local_info')
        if (isMtfLocalInfo) {
          const symbol = params.get('symbol') ?? 'BTCUSDT'
          const wsRoot = path.join(server.config.root, '..', '..')
          const script = path.join(wsRoot, 'backtest', 'mtf_spot_backtest.py')
          const py = spawn(pyCmd, [script, '--symbol', symbol, '--info'])
          let out = ''; let err = ''
          py.stdout.on('data', (d: Buffer) => { out += d.toString() })
          py.stderr.on('data', (d: Buffer) => { err += d.toString() })
          py.on('error', (e: Error) => {
            res.writeHead(500, { 'Content-Type': 'application/json' })
            res.end(JSON.stringify({ error: e.message }))
          })
          py.on('close', (code: number) => {
            if (code !== 0 || !out.trim()) {
              res.writeHead(500, { 'Content-Type': 'application/json' })
              res.end(JSON.stringify({ error: err.trim() || `exit ${code}` }))
              return
            }
            res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
            res.end(out.trim())
          })
          return
        }

        // ── MTF Local: corre backtest desde parquet local (no Supabase) ──────
        const isMtfLocalLongsInfo = url.startsWith('/api/backtest/mtf_local_longs_info')
        if (isMtfLocalLongsInfo) {
          const symbol = params.get('symbol') ?? 'BTCUSDT'
          const wsRoot = path.join(server.config.root, '..', '..')
          const script = path.join(wsRoot, 'backtest', 'mtf_spot_longs_backtest.py')
          const py = spawn(pyCmd, [script, '--symbol', symbol, '--info'])
          let out = ''; let err = ''
          py.stdout.on('data', (d: Buffer) => { out += d.toString() })
          py.stderr.on('data', (d: Buffer) => { err += d.toString() })
          py.on('error', (e: Error) => {
            res.writeHead(500, { 'Content-Type': 'application/json' })
            res.end(JSON.stringify({ error: e.message }))
          })
          py.on('close', (code: number) => {
            if (code !== 0 || !out.trim()) {
              res.writeHead(500, { 'Content-Type': 'application/json' })
              res.end(JSON.stringify({ error: err.trim() || `exit ${code}` }))
              return
            }
            res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
            res.end(out.trim())
          })
          return
        }

        if (isMtfLocalLongs) {
          const symbol = params.get('symbol') ?? 'BTCUSDT'
          const wsRoot = path.join(server.config.root, '..', '..')
          const script = path.join(wsRoot, 'backtest', 'mtf_spot_longs_backtest.py')
          console.log(`[backtest/mtf_local_longs] ${symbol} --days ${days}`)
          const py = spawn(pyCmd, [script, '--symbol', symbol, '--days', days, '--json'])
          let out = ''; let err = ''
          py.stdout.on('data', (d: Buffer) => { out += d.toString() })
          py.stderr.on('data', (d: Buffer) => { err += d.toString() })
          py.on('error', (e: Error) => {
            res.writeHead(500, { 'Content-Type': 'application/json' })
            res.end(JSON.stringify({ error: `spawn error: ${e.message}` }))
          })
          py.on('close', (code: number) => {
            if (code !== 0 || !out.trim()) {
              res.writeHead(500, { 'Content-Type': 'application/json' })
              res.end(JSON.stringify({ error: err.trim() || `exit code ${code}` }))
              return
            }
            res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
            res.end(out.trim())
          })
          return
        }

        if (isMtfLocal) {
          const symbol = params.get('symbol') ?? 'BTCUSDT'
          const wsRoot = path.join(server.config.root, '..', '..')
          const script = path.join(wsRoot, 'backtest', 'mtf_spot_backtest.py')
          console.log(`[backtest/mtf_local] ${symbol} --days ${days}`)
          const py = spawn(pyCmd, [script, '--symbol', symbol, '--days', days, '--json'])
          let out = ''; let err = ''
          py.stdout.on('data', (d: Buffer) => { out += d.toString() })
          py.stderr.on('data', (d: Buffer) => { err += d.toString() })
          py.on('error', (e: Error) => {
            res.writeHead(500, { 'Content-Type': 'application/json' })
            res.end(JSON.stringify({ error: `spawn error: ${e.message}` }))
          })
          py.on('close', (code: number) => {
            if (code !== 0 || !out.trim()) {
              res.writeHead(500, { 'Content-Type': 'application/json' })
              res.end(JSON.stringify({ error: err.trim() || `exit code ${code}` }))
              return
            }
            res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
            res.end(out.trim())
          })
          return
        }

        // ── HTF Combined: corre ambos scripts en paralelo y fusiona resultados ──
        if (isMtfCombined) {
          console.log(`[backtest/mtf_combined] corriendo shorts + longs --days ${days}`)
          const apiDir = path.join(server.config.root, 'api')
          const runScript = (name: string): Promise<string> => new Promise((resolve, reject) => {
            const py = spawn(pyCmd, [path.join(apiDir, name), '--days', days])
            let out = ''; let err = ''
            py.stdout.on('data', (d: Buffer) => { out += d.toString() })
            py.stderr.on('data', (d: Buffer) => { err += d.toString() })
            py.on('error', reject)
            py.on('close', (code) => {
              if (code !== 0 || !out.trim()) reject(new Error(err.slice(0, 300)))
              else resolve(out.trim())
            })
          })
          Promise.all([
            runScript('mtf_shorts_backtest.py'),
            runScript('mtf_longs_backtest.py'),
          ]).then(([shortsRaw, longsRaw]) => {
            const s = JSON.parse(shortsRaw)
            const l = JSON.parse(longsRaw)
            const allTrades = [...(s.trades ?? []), ...(l.trades ?? [])]
              .sort((a: any, b: any) => a.tsMs - b.tsMs)
              .map((t: any, i: number) => ({ ...t, idx: i + 1 }))
            const closed = allTrades.filter((t: any) => !t.isOpen)
            const wins   = closed.filter((t: any) => t.resultR > 0).length
            const totalR = closed.reduce((s: number, t: any) => s + (t.resultR ?? 0), 0)
            const CAPITAL = 500; const RISK = 10
            let equity = CAPITAL
            for (const t of closed) equity += (t.resultR ?? 0) * RISK
            const result = {
              trades: allTrades,
              n: closed.length,
              wins,
              total_r: +totalR.toFixed(2),
              avg_r: closed.length ? +(totalR / closed.length).toFixed(3) : 0,
              wr_pct: closed.length ? +(wins / closed.length * 100).toFixed(1) : 0,
              equity: +equity.toFixed(2),
              actual_days: Math.max(s.actual_days ?? 0, l.actual_days ?? 0),
            }
            res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
            res.end(JSON.stringify(result))
          }).catch((e: Error) => {
            res.writeHead(500, { 'Content-Type': 'application/json' })
            res.end(JSON.stringify({ error: e.message }))
          })
          return
        }

        const scriptName   = isBe         ? 'be_backtest_script.py'
                           : isSweep      ? 'sweep_backtest.py'
                           : isAbsorption ? 'absorption_backtest.py'
                           : isMtfLongs   ? 'mtf_longs_backtest.py'
                           : isMtfShorts  ? 'mtf_shorts_backtest.py'
                           : isLongs      ? 'longs_backtest.py'
                           : isShorts     ? 'mtf_shorts_backtest.py'
                           : 'backtest_script.py'
        const script = path.join(server.config.root, 'api', scriptName)

        const tag = isBe ? '/be' : isSweep ? '/sweep' : isAbsorption ? '/absorption'
                  : isMtfLongs ? '/mtf_longs' : isMtfShorts ? '/mtf_shorts'
                  : isLongs ? '/longs' : isShorts ? '/shorts' : ''
        console.log(`[backtest${tag}] corriendo ${pyCmd} ${script} --days ${days}`)

        const py = spawn(pyCmd, [script, '--days', days])

        let out = ''
        let err = ''
        py.stdout.on('data', (d: Buffer) => { out += d.toString() })
        py.stderr.on('data', (d: Buffer) => { err += d.toString() })

        py.on('error', (e) => {
          console.error('[backtest] spawn error:', e.message)
          res.writeHead(500, { 'Content-Type': 'application/json' })
          res.end(JSON.stringify({ error: `No se pudo iniciar Python: ${e.message}` }))
        })

        py.on('close', (code) => {
          if (code !== 0 || !out.trim()) {
            console.error('[backtest] error (code', code, '):', err.slice(0, 400))
            res.writeHead(500, { 'Content-Type': 'application/json' })
            res.end(JSON.stringify({ error: err.trim() || `Python salió con código ${code}` }))
            return
          }
          res.writeHead(200, {
            'Content-Type': 'application/json',
            'Cache-Control': 'no-store',
          })
          res.end(out.trim())
        })
      })
    },
  }
}

export default defineConfig({
  plugins: [react(), tailwindcss(), pythonBacktest()],
})
