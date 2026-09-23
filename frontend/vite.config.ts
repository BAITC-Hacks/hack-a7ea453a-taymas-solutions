import path from 'node:path'
import fs from 'node:fs'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const projectDir = path.dirname(fileURLToPath(import.meta.url))
const outputDir = path.resolve(projectDir, '../out')

// The Python pipeline writes ../out. This small adapter exposes it during local
// development and embeds available CSVs under dist/out for a static demo build.
export default defineConfig({
  plugins: [react(), {
    name: 'money-graph-output-files',
    configureServer(server) {
      server.middlewares.use('/out', (request, response, next) => {
        const name = request.url?.replace(/^\//, '')
        if (!name || name.includes('..')) return next()
        const file = path.join(outputDir, name)
        if (!fs.existsSync(file)) return next()
        response.setHeader('Content-Type', name.endsWith('.json') ? 'application/json; charset=utf-8' : 'text/csv; charset=utf-8')
        response.end(fs.readFileSync(file))
      })
    },
    generateBundle() {
      if (!fs.existsSync(outputDir)) return
      // resilience.json — необязательный предвычисленный бонус PAN-55; нет файла — пропускается
      for (const name of ['nodes_roles.csv', 'clusters.csv', 'top_nodes.csv', 'edge_table.csv', 'resilience.json']) {
        const file = path.join(outputDir, name)
        if (fs.existsSync(file)) this.emitFile({ type: 'asset', fileName: `out/${name}`, source: fs.readFileSync(file) })
      }
    },
  }],
  server: {
    fs: { allow: [path.resolve(projectDir, '..')] },
    proxy: { '/api': { target: 'http://127.0.0.1:8765', changeOrigin: true } },
  },
  preview: { proxy: { '/api': { target: 'http://127.0.0.1:8765', changeOrigin: true } } },
})
