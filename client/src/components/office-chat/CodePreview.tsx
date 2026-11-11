import { t, useLocale } from '../../i18n'
import React, { useEffect, useRef } from 'react'
import 'monaco-editor/esm/vs/base/browser/ui/codicons/codicon/codicon.css'
import * as monaco from 'monaco-editor/esm/vs/editor/editor.api'
import 'monaco-editor/esm/vs/editor/contrib/find/browser/findController'
import 'monaco-editor/esm/vs/basic-languages/javascript/javascript.contribution'
import 'monaco-editor/esm/vs/basic-languages/typescript/typescript.contribution'
import 'monaco-editor/esm/vs/basic-languages/css/css.contribution'
import 'monaco-editor/esm/vs/basic-languages/scss/scss.contribution'
import 'monaco-editor/esm/vs/basic-languages/less/less.contribution'
import 'monaco-editor/esm/vs/basic-languages/html/html.contribution'
import 'monaco-editor/esm/vs/basic-languages/xml/xml.contribution'
import 'monaco-editor/esm/vs/basic-languages/markdown/markdown.contribution'
import 'monaco-editor/esm/vs/basic-languages/python/python.contribution'
import 'monaco-editor/esm/vs/basic-languages/java/java.contribution'
import 'monaco-editor/esm/vs/basic-languages/yaml/yaml.contribution'
import 'monaco-editor/esm/vs/basic-languages/sql/sql.contribution'
import 'monaco-editor/esm/vs/basic-languages/shell/shell.contribution'
import 'monaco-editor/esm/vs/basic-languages/powershell/powershell.contribution'
import 'monaco-editor/esm/vs/basic-languages/go/go.contribution'
import 'monaco-editor/esm/vs/basic-languages/rust/rust.contribution'
import 'monaco-editor/esm/vs/basic-languages/cpp/cpp.contribution'
import 'monaco-editor/esm/vs/basic-languages/csharp/csharp.contribution'
import 'monaco-editor/esm/vs/basic-languages/php/php.contribution'
import 'monaco-editor/esm/vs/basic-languages/ini/ini.contribution'
import 'monaco-editor/esm/vs/language/json/monaco.contribution'
import EditorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker&inline'
import JsonWorker from 'monaco-editor/esm/vs/language/json/json.worker?worker&inline'

// Bundled locally, including workers: previews must also work offline/file://.
self.MonacoEnvironment = { getWorker: (_id, label) => label === 'json' ? new JsonWorker() : new EditorWorker() }

export default function CodePreview({ content, language, findRequest }: { content: string; language: string; findRequest: number }) {
  useLocale()
  const container = useRef<HTMLDivElement>(null)
  const editor = useRef<monaco.editor.IStandaloneCodeEditor | null>(null)
  useEffect(() => {
    if (!container.current) return
    const model = monaco.editor.createModel(content, language)
    const instance = monaco.editor.create(container.current, {
      model, theme: 'vs', readOnly: true, domReadOnly: true, automaticLayout: true,
      fontFamily: 'Consolas, "Microsoft YaHei", monospace', fontSize: 14, lineHeight: 23,
      minimap: { enabled: true, renderCharacters: false }, padding: { top: 20, bottom: 20 },
      scrollBeyondLastLine: false, contextmenu: false, links: false, folding: false,
      renderLineHighlight: 'none', wordWrap: 'off', ariaLabel: t('ui.file_source_read_only'),
    })
    editor.current = instance
    return () => { editor.current = null; instance.dispose(); model.dispose() }
  }, [content, language])
  useEffect(() => { if (findRequest > 0) { editor.current?.focus(); void editor.current?.getAction('actions.find')?.run() } }, [findRequest])
  return <div ref={container} data-office-code-preview style={{ flex: 1, minHeight: 0 }} />
}
