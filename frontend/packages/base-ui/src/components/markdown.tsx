import { useId } from 'react'
import ReactMarkdown, { defaultUrlTransform, type ExtraProps, type Options } from 'react-markdown'
import { useInRouterContext, useLocation } from 'react-router-dom'

type MarkdownProps = Omit<Options, 'urlTransform'> & {
  /** Stable, document-wide unique identity for content with persistent links. */
  documentId?: string
}
type MarkdownElement = NonNullable<ExtraProps['node']>

function scopeFootnoteLabel(prefix: string) {
  return (tree: Pick<MarkdownElement, 'children'>) => {
    function visit(node: MarkdownElement['children'][number]) {
      if (node.type !== 'element') return
      // remark-rehype's clobberPrefix scopes footnote IDs and links, but its
      // accessibility label is hard-coded. Scope only those generated nodes;
      // ordinary author headings and fragment links must stay unchanged.
      if (node.properties.dataFootnotes) {
        for (const child of node.children) {
          if (child.type === 'element' && child.properties.id === 'footnote-label') {
            child.properties.id = `${prefix}footnote-label`
          }
        }
      }
      if (node.properties.dataFootnoteRef) {
        const labels = node.properties.ariaDescribedBy
        if (Array.isArray(labels)) {
          node.properties.ariaDescribedBy = labels.map(label =>
            label === 'footnote-label' ? `${prefix}footnote-label` : label,
          )
        }
      }
      node.children.forEach(visit)
    }
    tree.children.forEach(visit)
  }
}

function documentUrlTransform(url: string, key: string): string {
  const safeUrl = defaultUrlTransform(url)
  // The runtime <base> serves assets from the deployment root. Bare fragments
  // must instead target the current document, preserving its path and query.
  return key === 'href' && safeUrl.startsWith('#') && typeof window !== 'undefined'
    ? new URL(safeUrl, window.location.href).href
    : safeUrl
}

function RoutedMarkdown(props: MarkdownProps) {
  // Subscribe below memoized content so navigation also refreshes unchanged
  // Markdown. window.location includes the public basename exactly once.
  useLocation()
  return <ReactMarkdown {...props} urlTransform={documentUrlTransform} />
}

/** Shared Markdown renderer; retain native links and the default URL safety filter. */
export default function Markdown({ documentId, ...props }: MarkdownProps) {
  const instanceId = useId()
  // Persistent documents keep native fragments valid after route teardown or
  // reload. Other instances still get stable, distinct streaming identities.
  // Encode every code point without collisions and retain the clobber guard.
  const namespace = documentId === undefined
    ? `instance-${instanceId}`
    : `document-${Array.from(documentId, char => char.codePointAt(0)!.toString(16)).join('-')}`
  const footnotePrefix = `user-content-${namespace}-`
  const scopedProps: Options = {
    ...props,
    remarkRehypeOptions: { ...props.remarkRehypeOptions, clobberPrefix: footnotePrefix },
    rehypePlugins: [[scopeFootnoteLabel, footnotePrefix], ...(props.rehypePlugins || [])],
  }
  return useInRouterContext()
    ? <RoutedMarkdown {...scopedProps} />
    : <ReactMarkdown {...scopedProps} urlTransform={documentUrlTransform} />
}
