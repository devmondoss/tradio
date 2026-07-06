import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Standalone orderflow terminal — own port so it never clashes with trade-lab.
export default defineConfig({
  plugins: [react()],
  server: { port: 5180 },
})
