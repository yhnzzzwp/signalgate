import { FRONTIER_RULE_TEXT } from "../labels";
import type { WorkflowClaim, WorkflowMetric, WorkflowPanel, WorkflowReport } from "../types";
import { ChronologyView } from "./ChronologyView";
import { FrontierAudit } from "./FrontierAudit";

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
    <li
      className={`workflow__claim workflow__claim--${claim.validation_status} ${
        claim.withdrawn ? "workflow__claim--withdrawn" : ""
      }`}
    >
      <p className="workflow__claim-text">{claim.statement}</p>
      <p className="event-card__source-meta">
        {KIND_TEXT[claim.kind] ?? claim.kind} ·{" "}
        {claim.withdrawn
          ? "ditarik analis setelah pemeriksaan"
          : (CLAIM_STATUS_TEXT[claim.validation_status] ?? claim.validation_status)}
        {claim.author === "code" ? " · ditulis kode" : ` · ${claim.author}`}
        {claim.version > 1 && ` · versi ${claim.version}`}
        {claim.metric_ids.length > 0 && ` · metrik: ${claim.metric_ids.join(", ")}`}
        {claim.source_ids.length > 0 && ` · sumber: ${claim.source_ids.join(", ")}`}
      </p>
      {claim.quote && <blockquote className="event-card__quote">“{claim.quote}”</blockquote>}
      {claim.reviewer_verdicts && claim.reviewer_verdicts.length > 1 && (
        <p className="event-card__source-meta">
          {claim.reviewer_conflict && <span className="workflow__conflict">pembanding berbeda pendapat · </span>}
          {claim.reviewer_verdicts
            .map(
              (item) =>
                `${(item.model ?? item.role).replace(/^ollama:/, "")}: ${
                  item.status ? (CLAIM_STATUS_TEXT[item.status] ?? item.status) : "tanpa putusan"
                }`,
            )
            .join(" · ")}
        </p>
      )}
      {claim.frontier && (
        <p className={`event-card__source-meta ${claim.frontier.applied ? "" : "frontier__shadow-note"}`}>
          Frontier ({claim.frontier.mode === "escalation" ? "escalation" : "shadow, tidak dipakai"}):{" "}
          {claim.frontier.frontier_status ? (CLAIM_STATUS_TEXT[claim.frontier.frontier_status] ?? claim.frontier.frontier_status) : "tanpa putusan sah"}
          {" · "}
          {FRONTIER_RULE_TEXT[claim.frontier.rule] ?? claim.frontier.rule}
          {claim.frontier.applied && ` · status akhir ${CLAIM_STATUS_TEXT[claim.frontier.final_status] ?? claim.frontier.final_status}`}
          {claim.frontier.independent?.reason && ` — “${claim.frontier.independent.reason}”`}
        </p>
      )}
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
  // Laporan lama hanya punya `reviewer_model` (satu nama); laporan baru mencatat urutan pembanding.
  const reviewers = (report.plan.reviewer_models ?? []).filter((name): name is string => Boolean(name));
  if (reviewers.length === 0 && report.plan.reviewer_model) reviewers.push(report.plan.reviewer_model);
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
            Analis {report.plan.analyst_model ?? "tidak ada"} · pembanding{" "}
            {reviewers.length > 0 ? reviewers.join(" → ") : "tidak ada"} · perbaikan {report.validation.repair_count}x
            {unresolved > 0 && ` · ${unresolved} klaim belum terverifikasi`}
          </p>
          {report.runtime?.bound && (
            <p className="event-card__source-meta">
              Inferensi: {report.runtime.bound.target === "local" ? "Perangkat lokal" : "Runtime lama"} ·{" "}
              {report.runtime.bound.ollama_url ?? "-"} · frontier{" "}
              {report.runtime.bound.frontier.enabled ? report.runtime.bound.frontier.mode : "mati"}
              {report.runtime.history.length > 1 && ` · ${report.runtime.history.length - 1}x resume`}
            </p>
          )}
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

      {(report.chronology ?? []).length > 0 && <ChronologyView actions={report.chronology ?? []} />}

      <FrontierAudit record={report.frontier} />

      {report.runtime && report.runtime.history.length > 0 && (
        <details className="event-card__review">
          <summary>Riwayat konfigurasi run ({report.runtime.history.length})</summary>
          <ul className="event-card__issues">
            {report.runtime.history.map((entry, index) => (
              <li key={`${entry.event}-${entry.at}-${index}`}>
                {new Date(entry.at).toLocaleString("id-ID", { dateStyle: "short", timeStyle: "short" })} ·{" "}
                {entry.event}
                {entry.from_nodes.length > 0 && ` dari node ${entry.from_nodes.join(", ")}`}
                {entry.snapshot && ` · ${entry.snapshot.target} ${entry.snapshot.ollama_url ?? ""}`}
                {(entry.changes ?? []).map((change) => (
                  <span key={change.field} className="event-card__source-meta">
                    {" "}
                    · {change.field}: {JSON.stringify(change.before)} → {JSON.stringify(change.after)}
                  </span>
                ))}
              </li>
            ))}
          </ul>
        </details>
      )}

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
