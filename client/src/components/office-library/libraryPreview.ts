// Original files are fetched on demand; metadata is persisted by the library.
export type LibraryFormat = 'TXT' | 'MD' | 'PDF' | 'DOCX'
export type LibraryBook = { id: string; name: string; format: LibraryFormat; color: number; file: File; size?: number; part?: number; location?: string; quote?: string }
export const MAX_PREVIEW_BYTES = 20 * 1024 * 1024
export const MAX_TEXT_PREVIEW_BYTES = 2 * 1024 * 1024
export function previewFormat(name: string): LibraryFormat | null {
  const extension = name.split('.').pop()?.toLowerCase()
  return ({ txt: 'TXT', md: 'MD', markdown: 'MD', pdf: 'PDF', docx: 'DOCX' } as Record<string, LibraryFormat>)[extension || ''] || null
}
export function previewFileSize(size: number) {
  return size < 1024 * 1024 ? `${Math.max(1, Math.round(size / 1024))} KB` : `${(size / 1024 / 1024).toFixed(1)} MB`
}
