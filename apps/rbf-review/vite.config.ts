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
        const isBe   = url.startsWith('/api/backtest/be')
        const scriptName = isBe ? 'be_backtest_script.py' : 'backtest_script.py'
        const script = path.join(server.config.root, 'api', scriptName)

        // Windows puede tener el ejecutable como 'python' o 'py'
        const pyCmd = process.platform === 'win32' ? 'python' : 'python3'

        console.log(`[backtest${isBe ? '/be' : ''}] corriendo ${pyCmd} ${script} --days ${days}`)

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
