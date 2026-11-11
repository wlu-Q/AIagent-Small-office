import { t } from '../../i18n'
export type LibraryDocument = {
  id: string; filename: string; display_name: string; extension: string; size: number;
  active: string | null; pending: string | null; version_id: string;
  status: 'queued' | 'processing' | 'ready' | 'failed' | 'staged'; error: string;
}
export type LibrarySettings = {
  embedding_url: string; embedding_model: string; rerank_url: string; rerank_model: string;
  rebuilding?: boolean; embeddingApiKeyConfigured: boolean; rerankApiKeyConfigured: boolean;
  embedding_api_key?: string; rerank_api_key?: string;
}
export type LibraryAction = 'list' | 'upload' | 'file' | 'delete' | 'retry' | 'settings' | 'saveSettings' | 'test'
export type LibraryRequest = { action: LibraryAction; id?: string; version?: string; part?: number; body?: unknown }

async function request<T>(payload: LibraryRequest): Promise<T> {
  const bridge = window.officeDesktopBridge
  if (!bridge?.officeLibrary) throw new Error(t('ui.library_service_disconnected_restart_the_desktop_app'))
  return bridge.officeLibrary(payload) as Promise<T>
}

export const libraryApi = {
  list: () => request<{ documents: LibraryDocument[] }>({ action: 'list' }),
  settings: () => request<LibrarySettings>({ action: 'settings' }),
  saveSettings: (body: LibrarySettings) => request<LibrarySettings>({ action: 'saveSettings', body }),
  test: () => request<{ ok: boolean; dimensions: number }>({ action: 'test' }),
  remove: (id: string) => request({ action: 'delete', id }),
  retry: (id: string) => request({ action: 'retry', id }),
  async file(id: string, version?: string, part?: number): Promise<File & { location?: string; quote?: string }> {
    const result = await request<{ filename: string; content: string; location?: string; quote?: string }>({ action: 'file', id, version, part })
    return Object.assign(new File([Uint8Array.from(atob(result.content), c => c.charCodeAt(0))], result.filename), { location: result.location, quote: result.quote })
  },
  async upload(file: File, replace?: LibraryDocument) {
    const content = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(String(reader.result).split(',')[1])
      reader.onerror = () => reject(new Error(t('ui.unable_to_read_file')))
      reader.readAsDataURL(file)
    })
    return request<{ id: string; conflict?: { id: string; filename: string; active: string | null; pending: string | null } }>({
      action: 'upload', body: { filename: file.name, content, replace_id: replace?.id,
        expected_version: replace?.pending || replace?.active },
    })
  },
}

export function libraryReference(value: string) {
  const match = /^office-library:\/\/([a-f0-9]{32})\/([a-f0-9]{32})(?:\?part=(\d+))?$/.exec(value)
  return match ? { id: match[1], version: match[2], part: Number(match[3] || 0) } : null
}
