import './chart.css'

/** The signature element: a red-pen circle, the mark a teacher leaves beside a wrong answer. */
export function RedPenMark() {
  return (
    <svg className="red-pen-mark" width="22" height="22" viewBox="0 0 22 22" aria-hidden="true">
      <path d="M11 2.5 C5 2, 2 6, 2.5 11 C3 16.5, 7 19.5, 11.5 19.3 C17 19, 20 14.5, 19.3 10 C18.7 5.8, 15 2, 11 2.5" />
    </svg>
  )
}

export type MarkerTone = 'alert' | 'caution' | 'resolved' | 'neutral'

export function SectionHeading({ marker, children }: { marker: MarkerTone; children: React.ReactNode }) {
  return (
    <div className="section-heading">
      <span className={`section-marker marker-${marker}`} />
      <h2>{children}</h2>
    </div>
  )
}

export function SectionBlock({ marker, title, children }: { marker: MarkerTone; title: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="section-block">
      <SectionHeading marker={marker}>{title}</SectionHeading>
      {children}
    </div>
  )
}

/** A chart's stamped header: kicker line, case number, title, meta row, thick closing rule. */
export function Masthead({ kicker, caseId, title, meta, animate = true }: {
  kicker: string
  caseId?: string
  title: React.ReactNode
  meta?: React.ReactNode
  animate?: boolean
}) {
  return (
    <header className={`masthead${animate ? ' chart-rise' : ''}`}>
      <div className="masthead-top">
        <span className="masthead-kicker">{kicker}</span>
        {caseId && <span className="masthead-case">{caseId}</span>}
      </div>
      <h1 className="masthead-title">{title}</h1>
      {meta && <div className="masthead-meta">{meta}</div>}
    </header>
  )
}

export function VitalsStrip({ children, animate = true }: { children: React.ReactNode; animate?: boolean }) {
  return <section className={`vitals-strip${animate ? ' chart-rise' : ''}`}>{children}</section>
}

export function Vital({ label, value, tone }: { label: string; value: React.ReactNode; tone?: 'alert' | 'resolved' | 'caution' }) {
  return (
    <div className="vital">
      <div className="vital-label">{label}</div>
      <div className={`vital-value${tone ? ` is-${tone}` : ''}`}>{value}</div>
    </div>
  )
}
