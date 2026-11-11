// Keep paths in their original namespace (project-relative, virtual or native).
// Authorization and realpath/symlink checks remain on the Gateway.
export function normalizeFileReferencePath(value: string): string {
  const path = value.replace(/\\/g, '/')
  const root = path.match(/^(?:[a-z]:\/|\/\/[^/]+\/[^/]+\/?|\/)/i)?.[0] || ''
  const parts: string[] = []
  for (const part of path.slice(root.length).split('/')) {
    if (!part || part === '.') continue
    if (part === '..' && parts.length && parts[parts.length - 1] !== '..') parts.pop()
    else parts.push(part)
  }
  return root + (root && !root.endsWith('/') && parts.length ? '/' : '') + parts.join('/')
}

export function resolveFileReferencePath(path: string, basePath?: string, workspaceId?: string): string {
  if (workspaceId && /^[a-zA-Z0-9_-]+$/.test(workspaceId)) {
    const normalized = path.replace(/\\/g, '/')
    if (/^\/mnt\/user-data\/(workspace|outputs)(\/|$)/.test(normalized)) {
      return normalizeFileReferencePath(normalized.replace('/mnt/user-data/', `/mnt/office-workspaces/${workspaceId}/`))
    }
    if (!basePath && !/^(?:[a-z]:[/\\]|[/\\])/i.test(path)) {
      basePath = `/mnt/office-workspaces/${workspaceId}/workspace/.`
    }
  }
  if (!basePath || /^(?:[a-z]:[/\\]|[/\\])/i.test(path)) return normalizeFileReferencePath(path)
  const base = basePath.replace(/\\/g, '/')
  return normalizeFileReferencePath(base.slice(0, base.lastIndexOf('/') + 1) + path)
}
