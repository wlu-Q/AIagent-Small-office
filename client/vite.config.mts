import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { cpSync, createReadStream, existsSync, mkdirSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const clientRoot = fileURLToPath(new URL('.', import.meta.url))

// PDF font/CMap data are shipped locally, including in offline desktop builds.
function pdfAssets(build: boolean) {
  let outputDirectory = resolve(clientRoot, 'dist')
  return {
    name: 'office-pdf-assets',
    configResolved(config: import('vite').ResolvedConfig) {
      outputDirectory = resolve(config.root, config.build.outDir)
    },
    configureServer(server: import('vite').ViteDevServer) {
      server.middlewares.use('/pdfjs-assets', (req, res, next) => {
        const match = req.url?.match(/^\/(cmaps|standard_fonts)\/(\w[\w.-]*\.(?:bcmap|pfb|ttf))$/)
        if (!match) return next()
        const path = resolve(clientRoot, 'node_modules/pdfjs-dist', match[1], match[2])
        if (!existsSync(path)) return next()
        res.setHeader('Content-Type', 'application/octet-stream')
        createReadStream(path).on('error', () => res.destroy()).pipe(res)
      })
    },
    closeBundle() {
      if (!build) return
      for (const folder of ['cmaps', 'standard_fonts']) {
        const destination = resolve(outputDirectory, 'pdfjs-assets', folder)
        mkdirSync(destination, { recursive: true })
        cpSync(resolve(clientRoot, 'node_modules/pdfjs-dist', folder), destination, { recursive: true })
      }
    },
  }
}

// Use relative asset paths for desktop/file:// builds while preserving normal dev behavior.
export default defineConfig(({ command }) => ({
  base: command === 'build' ? './' : '/',
  plugins: [react(), pdfAssets(command === 'build')],
}))
