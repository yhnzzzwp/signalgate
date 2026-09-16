import type { ScreenedEventSummary } from "../types";
import { VerdictBadge } from "./VerdictBadge";

const BUCKET_TEXT: Record<string, string> = {
  control_change: "Control Change",
  non_preemptive_capital: "Non-Preemptive Capital",
  rights_issue: "Rights Issue",
  general_action: "General Action",
};

export function EventCard({ event }: { event: ScreenedEventSummary }) {
  const { detail } = event;
  const needsReview = event.gate_status === "needs_review";

  return (
    <article className={`event-card ${needsReview ? "event-card--needs-review" : ""}`}>
      <header className="event-card__header">
        <span className="event-card__ticker">{event.ticker}</span>
        <VerdictBadge label={event.label} confidence={event.confidence} />
      </header>

      <h3 className="event-card__headline">{event.headline}</h3>

      <div className="event-card__meta">
        <span className="event-card__pill">{BUCKET_TEXT[event.bucket] ?? event.bucket}</span>
        <span className="event-card__pill event-card__pill--muted">via {event.provider}</span>
        {needsReview && <span className="event-card__pill event-card__pill--warning">Needs Review</span>}
      </div>

      {detail.snapshot && (
        <div className="event-card__snapshot">
          <span>{detail.snapshot.company_name}</span>
          {detail.snapshot.pb_ratio !== null && <span>PBV {detail.snapshot.pb_ratio.toFixed(1)}x</span>}
          {detail.snapshot.major_shareholders[0] && (
            <span>Top holder: {detail.snapshot.major_shareholders[0].name}</span>
          )}
        </div>
      )}

      {detail.verdict.rationale_bullets.length > 0 && (
        <ul className="event-card__rationale">
          {detail.verdict.rationale_bullets.map((bullet) => (
            <li key={bullet}>{bullet}</li>
          ))}
        </ul>
      )}

      {detail.research && (
        <details>
          <summary>Riset: {detail.research.status} · {detail.research.extraction_attempts} ekstraksi</summary>
          <ul>
            {detail.research.evidence.filter((source) => /^https?:\/\//.test(source.url)).map((source) => (
              <li key={source.id}>
                <a href={source.url} target="_blank" rel="noreferrer">[{source.id}] {source.title}</a>
              </li>
            ))}
          </ul>
          {detail.research.issues.length > 0 && <p>Masih ada {detail.research.issues.length} hal yang perlu diperiksa.</p>}
        </details>
      )}

      <a className="event-card__source" href={/^https?:\/\//.test(event.source_url) ? event.source_url : undefined} target="_blank" rel="noreferrer">
        Sumber →
      </a>
    </article>
  );
}
