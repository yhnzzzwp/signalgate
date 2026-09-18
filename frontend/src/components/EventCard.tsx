import { BUCKET_TEXT, FACT_TOPIC_TEXT, RESEARCH_STATUS_TEXT, VALIDATOR_STATUS_TEXT } from "../labels";
import type { EvidenceSource, ResearchFact, ScreenedEventSummary } from "../types";
import { VerdictBadge } from "./VerdictBadge";

const isWebUrl = (url: string) => /^https?:\/\//.test(url);

/** Tautan yang membuka dokumen tepat di halaman kutipan, bila halamannya diketahui. */
function evidenceHref(source: EvidenceSource): string | undefined {
  if (!isWebUrl(source.url)) return undefined;
  const base = source.url.split("#")[0];
  return source.page_number ? `${base}#page=${source.page_number}` : source.url;
}

function evidenceLabel(source: EvidenceSource): string {
  return source.page_number ? `${source.id} · hal. ${source.page_number}` : source.id;
}

interface SourceGroup {
  title: string;
  url: string;
  kind: string;
  pages: EvidenceSource[];
}

/**
 * Bukti dikelompokkan per dokumen, tetapi setiap halaman tetap bisa dibuka: kutipan `[E007]` harus
 * bisa ditemukan di daftar ini. `kind=input` adalah kandidat yang belum diverifikasi, bukan bukti:
 * verifikasi fakta di backend tidak pernah menerimanya, dan tautannya sudah ada di "Sumber kandidat".
 */
function groupSources(sources: EvidenceSource[]): SourceGroup[] {
  const groups = new Map<string, SourceGroup>();
  for (const source of sources) {
    if (source.kind === "input" || !isWebUrl(source.url)) continue;
    const key = source.url.split("#")[0];
    const existing = groups.get(key);
    if (existing) existing.pages.push(source);
    else groups.set(key, { title: source.title || key, url: key, kind: source.kind, pages: [source] });
  }
  return [...groups.values()];
}

function EvidenceLink({ id, source }: { id: string; source?: EvidenceSource }) {
  const href = source ? evidenceHref(source) : undefined;
  if (!source || !href) return <span className="event-card__evidence-id">[{id}]</span>;
  return (
    <a
      className="event-card__evidence-id event-card__evidence-link"
      href={href}
      target="_blank"
      rel="noreferrer"
      title={source.title}
    >
      [{evidenceLabel(source)}]
    </a>
  );
}

function FactRow({ fact, source }: { fact: ResearchFact; source?: EvidenceSource }) {
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
          “{fact.quote}” <EvidenceLink id={fact.evidence_id} source={source} />
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
  const evidence = research?.evidence ?? [];
  const evidenceById = new Map(evidence.map((source) => [source.id, source]));
  const sources = groupSources(evidence);
  const cited = new Set(facts.map((fact) => fact.evidence_id));

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
              <FactRow
                key={`${fact.id}-${fact.topic}-${fact.value}`}
                fact={fact}
                source={evidenceById.get(fact.evidence_id)}
              />
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
                    {source.kind === "pdf" ? `PDF · ${source.pages.length} halaman dibaca` : "halaman web"}
                    {source.pages.some((page) => cited.has(page.id)) && " · dikutip:"}
                  </span>
                  <span className="event-card__source-pages">
                    {source.pages.filter((page) => cited.has(page.id)).map((page) => (
                      <a
                        key={page.id}
                        className="event-card__evidence-id event-card__evidence-link"
                        href={evidenceHref(page)}
                        target="_blank"
                        rel="noreferrer"
                      >
                        {evidenceLabel(page)}
                      </a>
                    ))}
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
