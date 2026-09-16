import { BUCKET_TEXT, FACT_TOPIC_TEXT, RESEARCH_STATUS_TEXT, VALIDATOR_STATUS_TEXT } from "../labels";
import type { EvidenceSource, ResearchFact, ScreenedEventSummary } from "../types";
import { VerdictBadge } from "./VerdictBadge";

/** Bukti dikelompokkan per dokumen; satu PDF panjang tidak perlu satu baris untuk tiap halaman. */
function groupSources(sources: EvidenceSource[]) {
  const groups = new Map<string, { title: string; url: string; kind: string; pages: number }>();
  for (const source of sources) {
    // `kind=input` adalah kandidat yang belum diverifikasi, bukan bukti pendukung: verifikasi fakta
    // di backend tidak pernah menerimanya. Ditampilkan terpisah agar tidak terbaca sebagai sumber.
    if (!/^https?:\/\//.test(source.url)) continue;
    const key = source.url.split("#")[0];
    const existing = groups.get(key);
    if (existing) existing.pages += 1;
    else groups.set(key, { title: source.title || key, url: key, kind: source.kind, pages: 1 });
  }
  return [...groups.values()];
}

function FactRow({ fact }: { fact: ResearchFact }) {
  const status = fact.validator_status ?? "unknown";
  return (
    <li className={`event-card__fact event-card__fact--${status}`}>
      <div className="event-card__fact-head">
        <span className="event-card__fact-topic">{FACT_TOPIC_TEXT[fact.topic] ?? fact.topic}</span>
        <span className="event-card__fact-value">{fact.value.replace(/_/g, " ")}</span>
        <span className="event-card__fact-status">{VALIDATOR_STATUS_TEXT[status] ?? status}</span>
      </div>
      {fact.claim && <p className="event-card__fact-claim">{fact.claim}</p>}
      {fact.quote && (
        <blockquote className="event-card__quote">
          “{fact.quote}” <span className="event-card__evidence-id">[{fact.evidence_id}]</span>
        </blockquote>
      )}
    </li>
  );
}

export function EventCard({ event }: { event: ScreenedEventSummary }) {
  const { detail } = event;
  const needsReview = event.gate_status === "needs_review";
  const research = detail.research;
  const facts = research?.facts ?? [];
  const issues = research?.issues ?? [];
  const sources = groupSources(research?.evidence ?? []);

  return (
    <article className={`event-card ${needsReview ? "event-card--needs-review" : ""}`}>
      <header className="event-card__header">
        <span className="event-card__ticker">{event.ticker}</span>
        <VerdictBadge label={event.label} confidence={event.confidence} />
      </header>

      <h3 className="event-card__headline">{event.headline}</h3>

      <div className="event-card__meta">
        <span className="event-card__pill">{BUCKET_TEXT[event.bucket] ?? event.bucket}</span>
        <span className="event-card__pill event-card__pill--muted">
          {detail.snapshot ? "Data emiten: Sectors" : "Sumber publik (Scrapling)"}
        </span>
        <span className="event-card__pill event-card__pill--muted">
          model {event.provider.replace(/^ollama:/, "")}
        </span>
        {needsReview && <span className="event-card__pill event-card__pill--warning">Perlu Pemeriksaan</span>}
      </div>

      {detail.verdict.summary && <p className="event-card__summary">{detail.verdict.summary}</p>}

      {detail.snapshot && (
        <div className="event-card__snapshot">
          <span>{detail.snapshot.company_name}</span>
          {detail.snapshot.pb_ratio !== null && <span>PBV {detail.snapshot.pb_ratio.toFixed(1)}x</span>}
          {detail.snapshot.major_shareholders[0] && (
            <span>Pemegang saham terbesar: {detail.snapshot.major_shareholders[0].name}</span>
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

      {issues.length > 0 && (
        <details className="event-card__review" open={needsReview}>
          <summary>Kenapa hasil ini perlu diperiksa ({issues.length})</summary>
          <ul className="event-card__issues">
            {issues.map((issue) => (
              <li key={issue}>{issue}</li>
            ))}
          </ul>
        </details>
      )}

      {facts.length > 0 && (
        <details className="event-card__review">
          <summary>Fakta dan kutipannya ({facts.length})</summary>
          <ul className="event-card__facts">
            {facts.map((fact) => (
              <FactRow key={`${fact.id}-${fact.topic}-${fact.value}`} fact={fact} />
            ))}
          </ul>
        </details>
      )}

      {research && (
        <details className="event-card__review">
          <summary>
            Sumber dan proses: {RESEARCH_STATUS_TEXT[research.status] ?? research.status} ·{" "}
            {research.extraction_attempts}x ekstraksi
            {research.review_rounds ? ` · ${research.review_rounds} ronde tinjauan` : ""}
          </summary>
          {sources.length > 0 && (
            <ul className="event-card__sources">
              {sources.map((source) => (
                <li key={source.url}>
                  <a href={source.url} target="_blank" rel="noreferrer">
                    {source.title}
                  </a>
                  <span className="event-card__source-meta">
                    {source.kind === "pdf" ? "PDF" : "halaman web"}
                    {source.pages > 1 && ` · ${source.pages} halaman`}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {research.model_runs && research.model_runs.length > 0 && (
            <p className="event-card__source-meta">
              Giliran model:{" "}
              {research.model_runs
                .filter((run) => run.seconds !== undefined)
                .map((run) => `${run.role} ${run.model.replace(/^ollama:/, "")} ${run.seconds}s`)
                .join(" → ")}
            </p>
          )}
        </details>
      )}

      <a
        className="event-card__source"
        href={/^https?:\/\//.test(event.source_url) ? event.source_url : undefined}
        target="_blank"
        rel="noreferrer"
      >
        Sumber kandidat →
      </a>
    </article>
  );
}
