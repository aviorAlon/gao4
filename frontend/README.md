# Exam Diagnosis — frontend

React 19 + TypeScript + Vite + antd. This is the web UI for the API backend in the repository root,
and the very same page that the Tauri desktop shell (`src-tauri/`) loads into a native window.

```bash
npm install
npm run dev            # http://localhost:5173, talks to the backend on 127.0.0.1:8765
npm run lint           # oxlint
npm run build          # tsc -b && vite build → dist/
npm run desktop:dev    # Tauri window with hot reload
npm run desktop:build  # installer under src-tauri/target/release/bundle/
```

Hard rule for every page: **nothing may depend on browser chrome** (no `<a href>` to downloads, no
`window.location`, no new tabs). The desktop window has none of it. See the "Desktop app" section
of the root [README](../README.md) for what to use instead.
