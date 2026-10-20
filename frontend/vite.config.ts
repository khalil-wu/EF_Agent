import {defineConfig} from 'vite';

export default defineConfig({
  define: {'process.env.NODE_ENV': JSON.stringify('production')},
  build: {
    outDir: '../gateway/internal/server/static',
    emptyOutDir: false,
    lib: {entry: 'src/transcript.tsx', name: 'TranscriptUI', formats: ['iife'], fileName: () => 'transcript-ui.js'},
    sourcemap: true,
    target: 'es2022'
  }
});
