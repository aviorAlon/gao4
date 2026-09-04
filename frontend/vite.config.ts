import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        // Without this the whole app shipped as one ~1.3 MB chunk. Vendor code changes rarely
        // and is shared by every page, so it gets its own long-cacheable chunks; the pages
        // themselves are split by React.lazy in App.tsx.
        manualChunks: (id) => {
          if (!id.includes('node_modules')) return undefined
          if (id.includes('/antd/') || id.includes('/@ant-design/') || id.includes('/@rc-component/') || id.includes('/rc-')) return 'vendor-antd'
          if (id.includes('/react') || id.includes('/scheduler/')) return 'vendor-react'
          return 'vendor'
        },
      },
    },
  },
})
