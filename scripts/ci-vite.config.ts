import base from '../frontend/vite.config'

// Only the test harness uses these ports; the product's Vite config is unchanged.
export default {
  ...base,
  server: {
    ...base.server,
    host: '127.0.0.1',
    port: Number(process.env.CI_UI_PORT ?? '15179'),
    strictPort: true,
    proxy: {
      '/api': {
        target: `http://127.0.0.1:${process.env.CI_API_PORT ?? '18769'}`,
        changeOrigin: true,
      },
    },
  },
}
