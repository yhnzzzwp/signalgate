import { STAGE_TEXT } from "../labels";
import type { AuditLogEntry } from "../types";

/**
 * Ringkasan yang bisa dibaca sekilas; JSON mentah tetap tersedia sebagai opsi teknis.
 *
 * Satu baris JSON panjang per entri tidak terbaca di ponsel dan menyembunyikan hal yang justru
 * dicari pembaca: apa yang terjadi pada tahap itu.
 */
function summarise(entry: AuditLogEntry): string {
  const detail = (entry.detail ?? {}) as Record<string, unknown>;
  switch (entry.stage) {
    case "scan":
      return `${detail.candidates ?? 0} kandidat · ${detail.articles_checked ?? 0} artikel diperiksa`;
    case "sense":
      return `${(detail.selected as string[] | undefined)?.length ?? 0} dari ${detail.event_count ?? 0} event dipilih`;
    case "gate": {
      const terms = (detail.rejected_terms as string[] | undefined) ?? [];
      return terms.length > 0 ? `ditahan: ${terms.join(", ")}` : String(detail.status ?? "lolos");
    }
    case "research":
      return [detail.status, detail.extraction_attempts ? `${detail.extraction_attempts}x ekstraksi` : null]
        .filter(Boolean)
        .join(" · ");
    case "validate":
      return `${(detail.issues as string[] | undefined)?.length ?? 0} masalah numerik`;
    default:
      return "";
  }
}

interface AuditTrailProps {
  entries: AuditLogEntry[];
  total: number;
  hasMore: boolean;
  onLoadMore: () => void;
}

export function AuditTrail({ entries, total, hasMore, onLoadMore }: AuditTrailProps) {
  return (
    <div className="audit-trail">
      <h2 className="audit-trail__title">Jejak Audit</h2>
      <p className="audit-trail__subtitle">
        Setiap keputusan SENSE / VALIDATE / GATE tercatat, tidak ada yang tersembunyi.
      </p>
      {entries.length === 0 && <p className="dashboard__empty">Belum ada jejak audit.</p>}
      <ul className="audit-trail__list">
        {entries.map((entry) => (
          <li key={entry.id} className="audit-trail__item">
            <div className="audit-trail__row">
              <time className="audit-trail__time" dateTime={entry.created_at}>
                {/* Intl menolak timeZoneName bila digabung dateStyle/timeStyle, jadi komponennya disebut satu-satu. */}
                {new Date(entry.created_at).toLocaleString("id-ID", {
                  day: "2-digit",
                  month: "2-digit",
                  year: "2-digit",
                  hour: "2-digit",
                  minute: "2-digit",
                  second: "2-digit",
                  hour12: false,
                  timeZoneName: "short",
                })}
              </time>
              <span className={`audit-trail__stage audit-trail__stage--${entry.stage}`}>
                {STAGE_TEXT[entry.stage] ?? entry.stage}
              </span>
              <span className="audit-trail__ticker">{entry.ticker === "*" ? "—" : entry.ticker}</span>
              <span className="audit-trail__summary">{summarise(entry)}</span>
            </div>
            <details className="audit-trail__raw">
              <summary>JSON mentah</summary>
              <pre>{JSON.stringify(entry.detail, null, 2)}</pre>
            </details>
          </li>
        ))}
      </ul>
      {entries.length > 0 && (
        <footer className="dashboard__paging">
          <span>
            Menampilkan {entries.length} dari {total} entri, terbaru lebih dulu
          </span>
          {hasMore && (
            <button className="dashboard__filter" onClick={onLoadMore}>
              Muat jejak lebih lama
            </button>
          )}
        </footer>
      )}
    </div>
  );
}
