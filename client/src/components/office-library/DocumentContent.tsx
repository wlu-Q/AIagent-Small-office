import { t, useLocale } from '../../i18n'
import React, { useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { LibraryBook } from './libraryPreview'
import { MAX_TEXT_PREVIEW_BYTES } from './libraryPreview'

// PDF pages are rasterised only near the viewport; long documents do not keep
// hundreds of full-size canvases alive. No PDF actions or links are executed.
function PdfPage({ pdf, number }: { pdf: import('pdfjs-dist').PDFDocumentProxy; number: number }) {
  useLocale()
  const host = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const [visible, setVisible] = useState(false)
  const [error, setError] = useState(false)
  useEffect(() => {
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting), {
      root: host.current?.closest('.library-reading'), rootMargin: '500px',
    })
    if (host.current) observer.observe(host.current)
    return () => observer.disconnect()
  }, [])
  useEffect(() => {
    const element = canvas.current
    const container = host.current
    if (!visible || !element || !container) return
    let cancelled = false
    let render: import('pdfjs-dist').RenderTask | undefined
    let page: import('pdfjs-dist').PDFPageProxy | undefined
    const draw = async () => {
      try {
        page = await pdf.getPage(number)
        if (cancelled) return
        const natural = page.getViewport({ scale: 1 })
        const scale = Math.min(1000, container.clientWidth) / natural.width
        const viewport = page.getViewport({ scale })
        container.style.aspectRatio = `${natural.width} / ${natural.height}`
        const density = Math.min(window.devicePixelRatio || 1, 2)
        element.width = Math.ceil(viewport.width * density)
        element.height = Math.ceil(viewport.height * density)
        render = page.render({ canvasContext: element.getContext('2d')!, viewport,
          transform: [density, 0, 0, density, 0, 0] })
        await render.promise
      } catch (reason) {
        if (!cancelled && (reason as Error).name !== 'RenderingCancelledException') setError(true)
      }
    }
    void draw()
    return () => {
      cancelled = true
      render?.cancel()
      // Wait until the renderer has stopped using the page before releasing it.
      void (render?.promise.catch(() => {}) || Promise.resolve()).then(() => page?.cleanup())
      element.width = 0; element.height = 0
    }
  }, [pdf, number, visible])
  return <div ref={host} className="library-pdf-page" role="img" data-pdf-page={number} aria-label={t('ui.page_value', { value1: (number) })}>
    <canvas ref={canvas} />{error && <p role="alert">{t('ui.page')}{number} {t('ui.failed_to_load_reopen_the_file')}</p>}
    <span className="library-page-number">{number}</span>
  </div>
}

export default function DocumentContent({ book }: { book: LibraryBook }) {
  useLocale()
  const [text, setText] = useState('')
  const [docx, setDocx] = useState('')
  const [pdf, setPdf] = useState<import('pdfjs-dist').PDFDocumentProxy | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  useEffect(() => {
    let disposed = false
    let passwordRequired = false
    let task: import('pdfjs-dist').PDFDocumentLoadingTask | undefined
    const load = async () => {
      try {
        if (book.format === 'MD' || book.format === 'TXT') {
          if (book.file.size > MAX_TEXT_PREVIEW_BYTES) throw new Error(t('ui.text_exceeds_2_mb_preview_a_smaller_file'))
          const bytes = new Uint8Array(await book.file.arrayBuffer())
          let content: string
          if (bytes[0] === 0xff && bytes[1] === 0xfe) content = new TextDecoder('utf-16le').decode(bytes)
          else if (bytes[0] === 0xfe && bytes[1] === 0xff) content = new TextDecoder('utf-16be').decode(bytes)
          else {
            try { content = new TextDecoder('utf-8', { fatal: true }).decode(bytes) }
            catch { content = new TextDecoder('gb18030').decode(bytes) }
          }
          if (!disposed) setText(content)
        } else if (book.format === 'PDF') {
          const [library, worker, data] = await Promise.all([
            import('pdfjs-dist'), import('pdfjs-dist/build/pdf.worker.min.mjs?url'), book.file.arrayBuffer(),
          ])
          if (disposed) return
          library.GlobalWorkerOptions.workerSrc = worker.default
          const assets = new URL(`${import.meta.env.BASE_URL}pdfjs-assets/`, location.href).href
          task = library.getDocument({ data, isEvalSupported: false, useSystemFonts: true,
            cMapUrl: `${assets}cmaps/`, cMapPacked: true, standardFontDataUrl: `${assets}standard_fonts/` })
          task.onPassword = () => {
            passwordRequired = true
            if (!disposed) { setError(t('ui.this_pdf_is_password_protected_remove_the_password_locally_and_select_it')); setLoading(false) }
            void task?.destroy().catch(() => {})
          }
          const document = await task.promise
          if (!disposed) setPdf(document)
        } else {
          const { renderAsync } = await import('docx-preview')
          if (disposed) return
          const detached = document.implementation.createHTMLDocument('')
          await renderAsync(await book.file.arrayBuffer(), detached.body, detached.head, {
            useBase64URL: true, renderAltChunks: false, ignoreWidth: true,
            ignoreHeight: true, breakPages: true, ignoreFonts: true,
          })
          if (disposed) return
          // Never mount document-supplied styles or links into the app. The
          // preview has an opaque sandbox origin and cannot load any network URL.
          detached.querySelectorAll('script, iframe, object, embed, form, link, meta').forEach(node => node.remove())
          detached.querySelectorAll('*').forEach(node => {
            for (const attribute of Array.from(node.attributes)) {
              if (/^on/i.test(attribute.name) || ['href', 'xlink:href', 'srcset', 'action', 'formaction'].includes(attribute.name)
                || (attribute.name === 'src' && !/^data:image\/(png|jpeg|gif|webp|bmp);base64,/i.test(attribute.value))) node.removeAttribute(attribute.name)
            }
          })
          const policy = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; base-uri 'none'; form-action 'none'"
          setDocx(`<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${policy}">${detached.head.innerHTML}<style>html,body{margin:0;color:#263c40;background:#f3f5f3;font-family:'Microsoft YaHei',sans-serif} .docx-wrapper{padding:24px!important;background:transparent!important}.docx-wrapper>section.docx{box-sizing:border-box;max-width:100%;margin:0 auto 20px!important;box-shadow:0 2px 16px #20383c12!important}img{max-width:100%;height:auto} @media(max-width:600px){.docx-wrapper{padding:8px!important}section.docx{padding:20px!important}}</style></head><body>${detached.body.innerHTML}</body></html>`)
        }
      } catch (reason) {
        if (!disposed && !passwordRequired) setError(reason instanceof Error && reason.message.startsWith(t('ui.text_exceeds')) ? reason.message
          : t('ui.unable_to_read_this_value_file_check_the_file_format_and_select', { value1: (book.format) }))
      } finally { if (!disposed) setLoading(false) }
    }
    void load()
    return () => { disposed = true; void task?.destroy().catch(() => {}) }
  }, [book])
  useEffect(() => {
    if (loading || !book.location) return
    const timer = setTimeout(() => {
      const page = /第\s*(\d+)\s*页/.exec(book.location || '')
      if (page) document.querySelector(`[data-pdf-page="${page[1]}"]`)?.scrollIntoView({block:'start'})
      else {
        const quote = book.quote?.split('\n').find(line => line.trim().length > 5)?.replace(/^#+\s*/, '').slice(0,80)
        if (quote) Array.from(document.querySelectorAll('.library-markdown p,.library-markdown li,.library-markdown h1,.library-markdown h2'))
          .find(element => element.textContent?.includes(quote))?.scrollIntoView({block:'center'})
      }
    }, 100)
    return () => clearTimeout(timer)
  }, [loading, book])
  if (loading) return <p className="library-reader-message" role="status">{t('ui.reading_file')}</p>
  if (error) return <p className="library-reader-message" role="alert">{error}</p>
  if (pdf) return <div className="library-reading library-pdf" key={book.id}>
    {Array.from({ length: pdf.numPages }, (_, index) => <PdfPage key={index} pdf={pdf} number={index + 1} />)}
  </div>
  if (docx) return <iframe className="library-docx" title={t('ui.value_original_document', { value1: (book.file.name) })} sandbox="" srcDoc={docx} />
  return <div className="library-reading">
    {book.format === 'MD' ? <div className="library-markdown"><Markdown remarkPlugins={[remarkGfm]} skipHtml components={{
      a: ({ children }) => <span>{children}</span>, img: ({ alt }) => <span>{t('ui.image')}{alt || t('ui.not_loaded')}］</span>,
    }}>{text || t('ui.empty_document')}</Markdown></div> : <div className="library-prose">{text || t('ui.empty_document')}</div>}
  </div>
}
