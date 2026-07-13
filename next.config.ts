import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Necesario para el Dockerfile multi-stage: genera .next/standalone con
  // solo los archivos (y el subset de node_modules) que hacen falta para
  // correr `node server.js` en produccion, sin copiar el node_modules
  // completo a la imagen final.
  output: "standalone",
  // Fija la raiz del workspace a este proyecto. Sin esto, Turbopack detecta
  // un lockfile de otro proyecto en un directorio padre del filesystem y
  // emite un warning (inofensivo, pero ruidoso) sobre la raiz inferida.
  turbopack: {
    root: path.join(__dirname),
  },
};

export default nextConfig;
