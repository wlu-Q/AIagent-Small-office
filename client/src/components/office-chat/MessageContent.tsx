import { t, useLocale } from '../../i18n'
import { libraryReference } from '../office-library/libraryApi'
import React, { useEffect, useRef, useState } from 'react'
import styled from '@emotion/styled'
import Markdown, { defaultUrlTransform } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Element, Root, RootContent } from 'hast'
import DescriptionOutlined from '@mui/icons-material/DescriptionOutlined'
import CodeOutlined from '@mui/icons-material/CodeOutlined'
import ImageOutlined from '@mui/icons-material/ImageOutlined'
import ArticleOutlined from '@mui/icons-material/ArticleOutlined'
import { officeApi, type OfficeFilePreview } from '../../services/office/officeApi'
import { fileName, fileSize, useFilePreview } from './FilePreview'
import VisualizationBlock from './VisualizationBlock'
import { resolveFileReferencePath } from './fileReferencePath'

// Only these explicit fenced languages are visualized. Regular JSON, code and
// inline code retain their existing presentation. Keep the component identity
// stable across message refreshes so charts do not remount while polling Runs.
function MarkdownPre({ node, children, ...props }: React.ComponentProps<'pre'> & { node?: unknown }) {
  useLocale()
  const code = React.Children.count(children) === 1 ? React.Children.toArray(children)[0] : null
  if (React.isValidElement<{ className?: string; children?: React.ReactNode }>(code)) {
    const language = code.props.className?.match(/^language-(mermaid|office-chart)$/)?.[1]
    if ((language === 'mermaid' || language === 'office-chart') && typeof code.props.children === 'string') {
      return <VisualizationBlock language={language} source={code.props.children.replace(/\n$/, '')} />
    }
  }
  return <pre {...props}>{children}</pre>
}

const Content = styled.div`
  min-width: 0;
  overflow-wrap: anywhere;
  white-space: normal;
  line-height: 1.85;
  > :first-child { margin-top: 0; }
  > :last-child { margin-bottom: 0; }
  p { margin: 0 0 12px; white-space: pre-wrap; }
  h1, h2, h3, h4 { font-size: 1.1em; line-height: 1.5; margin: 20px 0 10px; color: #172b48; }
  h1 { font-size: 1.3em; }
  ul, ol { margin: 8px 0 14px; padding-left: 24px; }
  li { padding-left: 3px; }
  li > p { margin: 0; }
  blockquote { margin: 12px 0; padding: 4px 16px; border-left: 3px solid #b5cbe6; color: #627186; }
  code { padding: 2px 5px; border-radius: 4px; background: #edf1f6; font: .9em Consolas, monospace; }
  pre { max-width: 100%; overflow: auto; margin: 12px 0; padding: 16px; border-radius: 10px; background: #192537; color: #e5edf8; line-height: 1.65; }
  pre code { padding: 0; background: none; color: inherit; white-space: pre; overflow-wrap: normal; }
  .table-scroll { overflow-x: auto; max-width: 100%; }
  table { border-collapse: collapse; font-size: .93em; }
  th, td { padding: 8px 12px; border: 1px solid #dbe4ef; text-align: left; }
  th { background: #f1f5fa; }
  a { color: #2862ad; text-decoration: underline; text-underline-offset: 3px; }
  hr { margin: 18px 0; border: 0; border-top: 1px solid #e5eaf1; }
`

const FileButton = styled.button`
  display: inline-flex;
  align-items: center;
  gap: 12px;
  width: 280px;
  max-width: 100%;
  padding: 14px 16px;
  margin: 5px 10px 5px 0;
  border: 1px solid #dce9e2;
  border-radius: 12px;
  color: #2d4b3e;
  background: #edf5f0;
  font: inherit;
  line-height: 1.5;
  text-align: left;
  cursor: pointer;
  svg { flex: 0 0 auto; font-size: 28px; color: #3b9b85; }
  .file-label { display: flex; flex-direction: column; min-width: 0; }
  .file-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 600; }
  small { font-size: 12px; color: #718579; margin-top: 2px; }
  &:hover { border-color: #9fc4b4; background: #e4f0e9; }
  &:focus-visible { outline: 2px solid #247b70; outline-offset: 2px; }
  &:disabled { opacity: .65; cursor: wait; }
`

const FileLink = styled.button`
  display: inline;
  padding: 0;
  border: 0;
  background: none;
  color: #2862ad;
  font: inherit;
  text-align: inherit;
  text-decoration: underline;
  text-underline-offset: 3px;
  cursor: pointer;
  &:focus-visible { outline: 2px solid #247b70; outline-offset: 2px; }
  &:disabled { opacity: .65; cursor: wait; }
`

function standaloneFileLinks() {
  return (tree: Root) => {
    const visit = (node: Root | RootContent) => {
      if (node.type === 'element' && node.tagName === 'p') {
        const content = node.children.filter(child => child.type !== 'text' || child.value.trim())
        if (content.length && content.every(child => child.type === 'element' && child.tagName === 'a'
          && localFilePath(String(child.properties.href || '')))) {
          for (const child of content as Element[]) child.properties.officeFileCard = true
        }
      }
      if ('children' in node) node.children.forEach(visit)
    }
    visit(tree)
  }
}

export function localFilePath(url: string): string | null {
  let value: string
  try { value = decodeURIComponent(url).trim() } catch { return null }
  if (/^(?:file:|sandbox:)/i.test(value)) value = value.replace(/^(?:file:\/\/|sandbox:)/i, '')
  if (/^\/[a-z]:\//i.test(value)) value = value.slice(1)
  if (!value || /[\x00-\x1f]/.test(value) || /^[/\\]{2}/.test(value)) return null
  if (/^[a-z]:[/\\]/i.test(value)) return value.replace(/:\d+(?::\d+)?$/, '')
  if (/^[a-z][a-z\d+.-]*:/i.test(value) || value.startsWith('#')) return null
  return /[/\\]|\.[a-z\d]{1,12}(?:#.*)?$/i.test(value) ? value.replace(/#.*$/, '') : null
}

export function FileReference({ path, employeeId, runId, children, inline = false }: {
  path: string; employeeId?: string; runId?: string | null; children?: React.ReactNode; inline?: boolean
}) {
  useLocale()
  const openFile = useFilePreview()
  const button = useRef<HTMLButtonElement>(null)
  const [metadata, setMetadata] = useState<OfficeFilePreview | null>(null)
  const [error, setError] = useState('')
  const awaitingOwner = (path.startsWith('/mnt/office-workspaces/') || path.startsWith('/mnt/user-data/')) && !(runId && employeeId)
  const Icon = metadata?.kind === 'image' ? ImageOutlined : metadata?.language === 'markdown' ? ArticleOutlined
    : metadata?.kind === 'text' && metadata.language !== 'plaintext' ? CodeOutlined : DescriptionOutlined
  useEffect(() => {
    let canceled = false
    setMetadata(null); setError('')
    if (awaitingOwner || inline) return
    const observer = new IntersectionObserver(entries => {
      if (!entries.some(entry => entry.isIntersecting)) return
      observer.disconnect()
      officeApi.previewFile({ path, ...(runId && employeeId ? { employeeId, runId } : {}), metadataOnly: true })
        .then(result => { if (!canceled) setMetadata(result) })
        .catch(cause => { if (!canceled) setError(cause instanceof Error ? cause.message : String(cause)) })
    })
    if (button.current) observer.observe(button.current)
    return () => { canceled = true; observer.disconnect() }
  }, [path, employeeId, runId, awaitingOwner, inline])
  if (inline) return <FileLink type="button" data-office-file={path} disabled={awaitingOwner}
    title={awaitingOwner ? t('ui.locating_task_file') : t('ui.preview_file_value', { value1: (path) })}
    onClick={() => openFile({ path, ...(runId && employeeId ? { employeeId, runId } : {}) })}>
    {children || fileName(path)}
  </FileLink>
  return <>
    <FileButton ref={button} type="button" data-office-file={path} disabled={awaitingOwner}
      title={awaitingOwner ? t('ui.locating_task_file') : error || t('ui.preview_file_value', { value1: (path) })}
      onClick={() => openFile({ path,
        ...(runId && employeeId ? { employeeId, runId } : {}) })}>
      <Icon aria-hidden="true" /><span className="file-label"><span className="file-name">{metadata?.name || children || fileName(path)}</span>
        <small>{awaitingOwner ? t('ui.locating_task_file') : metadata ? fileSize(metadata.size) : error ? t('ui.unable_to_read_click_to_retry') : t('ui.file_preview')}</small></span>
    </FileButton>
  </>
}

function WebLink({ href, children }: { href: string; children: React.ReactNode }) {
  useLocale()
  const [error, setError] = useState('')
  return <><a href={href} title={t('ui.open_in_browser_value', { value1: (href) })} onClick={event => {
    event.preventDefault(); setError('')
    officeApi.openExternalUrl(href).catch(cause => setError(cause instanceof Error ? cause.message : String(cause)))
  }} onAuxClick={event => event.preventDefault()}>{children}</a>{error && <span role="alert" style={{ color: '#b54343', fontSize: 12 }}>（{error}）</span>}</>
}

function MessageContent({ text, employeeId, runId, basePath, workspaceId }: {
  text: string; employeeId?: string; runId?: string | null; basePath?: string; workspaceId?: string
}) {
  useLocale()
  const resolveFile = (href: string) => {
    const path = localFilePath(href)
    return path ? resolveFileReferencePath(path, basePath, workspaceId) : null
  }
  return <Content data-office-message-content>
    <Markdown remarkPlugins={[remarkGfm]} rehypePlugins={[standaloneFileLinks]} skipHtml
      urlTransform={(url) => (libraryReference(url) || localFilePath(url)) ? url : defaultUrlTransform(url)}
      components={{
        pre: MarkdownPre,
        a: ({ href, children, node }) => {
          if (href && libraryReference(href)) return <a href={href} onClick={event => {
            event.preventDefault(); window.dispatchEvent(new CustomEvent('office-library:open', {detail:href}))
          }} onAuxClick={event => event.preventDefault()}>{children}</a>
          const file = href ? resolveFile(href) : null
          if (file) return <FileReference inline={!node?.properties.officeFileCard} path={file} employeeId={employeeId} runId={runId}>{children}</FileReference>
          if (href && /^https?:\/\//i.test(href)) return <WebLink href={href}>{children}</WebLink>
          return <span title={href}>{children}{href && <>（{href}）</>}</span>
        },
        img: ({ src, alt }) => {
          const file = src ? resolveFile(src) : null
          return file ? <FileReference path={file} employeeId={employeeId} runId={runId}>{alt || fileName(file)}</FileReference> : <span>{t('ui.image')}{alt || t('ui.external_image_not_loaded')}］</span>
        },
        table: ({ children }) => <div className="table-scroll"><table>{children}</table></div>,
      }}
    >{text}</Markdown>
  </Content>
}

export default React.memo(MessageContent)
