import type { WorkflowClaim, WorkflowMetric, WorkflowPanel, WorkflowReport } from "../types";

const DOMAIN_TEXT: Record<string, string> = {
  fundamental: "Fundamental",
  valuation: "Valuasi",
  technical: "Technical",
  news: "Berita & aksi korporasi",
};

const PANEL_STATUS_TEXT: Record<string, string> = {
  completed: "lengkap",
  needs_review: "perlu pemeriksaan",
  insufficient_data: "data kurang",
  failed: "gagal",
};

const CLAIM_STATUS_TEXT: Record<string, string> = {
  supported: "terverifikasi",
  unsupported: "belum didukung bukti",
  contradicted: "dibantah bukti",
  pending: "belum dinilai",
};

const KIND_TEXT: Record<string, string> = {
  observation: "pengamatan",
  calculation: "perhitungan",
  interpretation: "tafsiran",
};

const METRIC_STATUS_TEXT: Record<string, string> = {
  insufficient_data: "data kurang",
  not_meaningful: "tidak bermakna",
};

function formatValue(metric: WorkflowMetric): string {
  if (metric.value === null) return METRIC_STATUS_TEXT[metric.status] ?? "tidak tersedia";
  const number = (value: number, digits: number) =>
    value.toLocaleString("id-ID", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  if (metric.unit === "ratio") return `${number(metric.value * 100, 1)}%`;
  if (metric.unit === "x") return `${number(metric.value, 2)}x`;
  if (metric.unit === "IDR") {
    const size = Math.abs(metric.value);
    if (size >= 1e12) return `Rp${number(metric.value / 1e12, 2)} T`;
    if (size >= 1e9) return `Rp${number(metric.value / 1e9, 1)} M`;
    return `Rp${number(metric.value, 0)}`;
  }
  return number(metric.value, 1);
}

function ClaimRow({ claim }: { claim: WorkflowClaim }) {
  return (
    <li className={`workflow__claim workflow__claim--${claim.validation_status}`}>
      <p className="workflow__claim-text">{claim.statement}</p>
      <p className="event-card__source-meta">
        {KIND_TEXT[claim.kind] ?? claim.kind} · {CLAIM_STATUS_TEXT[claim.validation_status] ?? claim.validation_status}
        {claim.author === "code" ? " · ditulis kode" : ` · ${claim.author}`}
        {claim.version > 1 && ` · versi ${claim.version}`}
        {claim.metric_ids.length > 0 && ` · metrik: ${claim.metric_ids.join(", ")}`}
        {claim.source_ids.length > 0 && ` · sumber: ${claim.source_ids.join(", ")}`}
      </p>
      {claim.quote && <blockquote className="event-card__quote">“{claim.quote}”</blockquote>}
      {claim.validation_notes.length > 0 && (
        <ul className="event-card__issues">
          {claim.validation_notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
    </li>
  );
}

function Panel({ panel, metrics }: { panel: WorkflowPanel; metrics: Record<string, WorkflowMetric> }) {
  const rows = panel.metrics.map((id) => metrics[id]).filter(Boolean);
  return (
    <article className={`workflow__panel workflow__panel--${panel.status}`}>
      <header className="workflow__panel-head">
        <h3>{DOMAIN_TEXT[panel.domain] ?? panel.domain}</h3>
        <span className={`reports__badge reports__badge--${panel.status}`}>
          {PANEL_STATUS_TEXT[panel.status] ?? panel.status}
        </span>
      </header>
      {panel.headline && <p className="workflow__headline">{panel.headline}</p>}

      {panel.claims.length > 0 && (
        <ul className="workflow__claims">
          {panel.claims.map((claim) => (
            <ClaimRow key={`${claim.claim_id}-${claim.version}`} claim={claim} />
          ))}
        </ul>
      )}

      {rows.length > 0 && (
        <details className="event-card__review">
          <summary>Metrik dan formulanya ({rows.length})</summary>
          {/* Daftar, bukan tabel: di dalam kolom panel yang sempit, formula pada sel tabel terpotong
              per karakter dan justru tidak terbaca. */}
          <ul className="workflow__metric-list">
            {rows.map((metric) => (
              <li key={metric.metric_id} className={metric.status !== "ok" ? "workflow__metric--empty" : ""}>
                <div className="workflow__metric-head">
                  <span>{metric.name}</span>
                  <strong>{formatValue(metric)}</strong>
                </div>
                <p className="event-card__source-meta">
                  {metric.period} · <code>{metric.formula}</code>
                  {metric.price_basis && ` · basis ${metric.price_basis}`}
                  {metric.note && ` · ${metric.note}`}
                </p>
              </li>
            ))}
          </ul>
        </details>
      )}

      {[
        ["Data yang kurang", panel.missing_data],
        ["Keterbatasan", panel.limitations],
        ["Konflik yang belum selesai", panel.conflicts],
      ]
        .filter(([, items]) => (items as string[]).length > 0)
        .map(([label, items]) => (
          <details key={label as string} className="event-card__review" open={label === "Konflik yang belum selesai"}>
            <summary>
              {label as string} ({(items as string[]).length})
            </summary>
            <ul className="event-card__issues">
              {(items as string[]).map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </details>
        ))}
    </article>
  );
}

export function WorkflowPanels({ report }: { report: WorkflowReport }) {
  const unresolved = report.validation.unresolved_claim_ids?.length ?? 0;
  return (
    <div className="workflow">
      <header className="workflow__summary">
        <div>
          <h3>
            {report.ticker} · {PANEL_STATUS_TEXT[report.status] ?? report.status}
          </h3>
          <p className="event-card__source-meta">
            Tanggal acuan {report.as_of} · {report.mode === "live" ? "mode live" : "tanggal acuan historis"} ·{" "}
            {report.data_mode === "replay" ? "snapshot diputar ulang" : "snapshot diambil untuk run ini"} · dibuat{" "}
            {new Date(report.generated_at).toLocaleString("id-ID", { dateStyle: "short", timeStyle: "short" })} ·{" "}
            {report.credits_used} kredit Sectors · versi laporan {report.report_version}
          </p>
          <p className="event-card__source-meta">
            Analis {report.plan.analyst_model ?? "tidak ada"} · pembanding {report.plan.reviewer_model ?? "tidak ada"} ·{" "}
            perbaikan {report.validation.repair_count}x
            {unresolved > 0 && ` · ${unresolved} klaim belum terverifikasi`}
          </p>
        </div>
      </header>

      {report.synthesis.sections.length > 0 && (
        <section className="workflow__synthesis">
          <h4>Ringkasan lintas dimensi {report.synthesis.author === "code" ? "(disusun kode)" : ""}</h4>
          {report.synthesis.sections.map((section) => (
            <p key={section.text}>
              {section.text}
              <span className="event-card__source-meta"> [{section.claim_ids.join(", ")}]</span>
            </p>
          ))}
          {report.synthesis.dropped.length > 0 && (
            <details className="event-card__review">
              <summary>Bagian ringkasan yang ditahan ({report.synthesis.dropped.length})</summary>
              <ul className="event-card__issues">
                {report.synthesis.dropped.map((item) => (
                  <li key={item.text}>
                    “{item.text}” — {item.issues.join(" ")}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </section>
      )}

      <div className="workflow__grid">
        {["fundamental", "valuation", "technical", "news"].map((domain) => {
          const panel = report.panels[domain];
          return panel ? <Panel key={domain} panel={panel} metrics={report.metrics} /> : null;
        })}
      </div>

      <details className="event-card__review">
        <summary>Sumber dan kesegaran data ({report.sources.length})</summary>
        <table className="workflow__metrics">
          <thead>
            <tr>
              <th>Sumber</th>
              <th>Status</th>
              <th>Diambil</th>
              <th>Tersedia sejak</th>
              <th>Kredit</th>
            </tr>
          </thead>
          <tbody>
            {report.sources.map((source) => (
              <tr key={source.source_id} className={source.status !== "ok" ? "workflow__metric--empty" : ""}>
                <td>
                  <code>{source.source_id}</code>
                  {source.error && <span className="event-card__source-meta"> — {source.error}</span>}
                </td>
                <td>{source.status}</td>
                <td>{source.fetched_at ? new Date(source.fetched_at).toLocaleString("id-ID") : "-"}</td>
                <td>{source.available_at ?? "tidak diketahui"}</td>
                <td>{source.credits ?? 0}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>

      <p className="dashboard__disclaimer">{report.disclaimer}</p>
    </div>
  );
}
