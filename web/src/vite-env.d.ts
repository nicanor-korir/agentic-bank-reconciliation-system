/// <reference types="vite/client" />

/**
 * Narrow the env typing. Vite's own `ImportMetaEnv` carries an `any` index
 * signature, and this project does not allow `any` on values that reach the UI.
 */
interface ImportMetaEnv {
  readonly VITE_API_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
