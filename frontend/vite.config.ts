import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({plugins:[react()],build:{rollupOptions:{output:{manualChunks(id){if(id.includes('pdfjs-dist'))return 'pdf'}}}},server:{proxy:{'/api':{target:'http://127.0.0.1:'+(process.env.TEST_API_PORT||'8000'),changeOrigin:false,xfwd:process.env.MULTIUSER_PROXY!=='true'}}}});
