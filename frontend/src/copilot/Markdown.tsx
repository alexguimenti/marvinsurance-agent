import ReactMarkdown from 'react-markdown'
import { Link } from 'react-router-dom'
import remarkGfm from 'remark-gfm'
import { linkClaims } from './linkClaims'

const CLAIM_ID = /CLM-\d{4}/

/** The Copilot's answers, shown formatted: short paragraphs, lists and small tables. Claim ids link to the claim's page. */
export default function Markdown({ text }: { text: string }) {
  return (
    <div className="cp-md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) =>
            href && href.startsWith('/claims/') && CLAIM_ID.test(href) ? (
              <Link to={href}>{children}</Link>
            ) : (
              <a href={href} target="_blank" rel="noreferrer noopener">{children}</a>
            ),
          table: ({ children }) => <div className="cp-table"><table>{children}</table></div>,
        }}
      >
        {linkClaims(text)}
      </ReactMarkdown>
    </div>
  )
}
