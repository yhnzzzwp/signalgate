import { useEffect, useState } from "react";
import { ApiError, activateRuntime, checkRuntime, fetchRuntimeConfig } from "../api/client";
import type { RuntimeCheck, RuntimeConfig, RuntimeFrontierMode } from "../types";

const GPU_TEXT: Record<string, string> = {
  proven: "terbukti di GPU",
  not_proven: "tidak di GPU (CPU)",
  unknown: "belum diketahui",
};

const OLLAMA_TEXT: Record<string, string> = {
  connected: "tersambung",
  unauthorized: "token ditolak",
  disconnected: "terputus",
  invalid_endpoint: "URL ditolak",
  redirect_refused: "redirect ditolak",
  not_checked: "belum dicek",
};

function Row({ label, ok, text, detail }: { label: string; ok: boolean | null; text: string; detail?: string | null }) {
  const state = ok === null ? "unknown" : ok ? "ok" : "bad";
  return (
    <li className={`runtime__row runtime__row--${state}`}>
      <span className="runtime__dot" aria-hidden="true" />
      <strong>{label}</strong>
      <span>{text}</span>
      {detail && <span className="event-card__source-meta">{detail}</span>}
    </li>
  );
}

/** Hasil cek: tiap lapisan dipisah supaya "backend hidup" tidak terbaca sebagai "GPU siap". */
function CheckView({ check }: { check: RuntimeCheck }) {
  const account = check.frontier.account;
  const balance = account?.balance?.balances?.[0];
  return (
    <ul className="runtime__checks">
      <Row label="Backend" ok={check.backend.ok} text="hidup" />
      <Row label="Endpoint" ok={check.endpoint.ok} text={check.endpoint.url ?? "tidak valid"} detail={check.endpoint.error} />
      <Row
        label="Ollama"
        ok={check.ollama.ok}
        text={`${OLLAMA_TEXT[check.ollama.status] ?? check.ollama.status}${check.ollama.version ? ` · v${check.ollama.version}` : ""}`}
        detail={check.ollama.error}
      />
      <Row
        label="Model"
        ok={check.ollama.ok ? check.models.ok && check.models.missing.length === 0 : null}
        text={
          !check.ollama.ok
            ? "belum bisa dicek"
            : !check.models.ok
              ? "daftar model tidak terbaca — kesiapan tidak bisa dipastikan"
              : check.models.missing.length > 0
                ? `belum diunduh: ${check.models.missing.join(", ")}`
                : `tersedia: ${check.models.required.join(", ")}`
        }
        detail={check.models.error}
      />
      <Row
        label="GPU"
        ok={check.gpu.status === "unknown" ? null : check.gpu.status === "proven"}
        text={GPU_TEXT[check.gpu.status] ?? check.gpu.status}
        detail={check.gpu.detail}
      />
      <Row
        label="Frontier"
        ok={check.frontier.enabled ? check.frontier.key_configured && !check.frontier.issue : null}
        text={
          check.frontier.enabled
            ? `${check.frontier.mode} · ${check.frontier.model_version ?? check.frontier.model} · key ${check.frontier.key_configured ? "terisi" : "kosong"}`
            : "mati"
        }
        detail={
          account
            ? account.key_valid
              ? `key diterima${balance ? ` · saldo ${balance.total_balance} ${balance.currency}` : ""}${
                  account.model_available === false ? " · model tidak ada di daftar akun" : ""
                } (cek metadata, tanpa biaya generasi)`
              : `key ditolak: ${account.error ?? "tidak diketahui"}`
            : check.frontier.issue
        }
      />
      {check.warnings.map((warning) => (
        <li key={warning} className="event-card__source-meta">
          ⚠ {warning}
        </li>
      ))}
    </ul>
  );
}

export function Runtime() {
  const [config, setConfig] = useState<RuntimeConfig | null>(null);
  const [frontierMode, setFrontierMode] = useState<RuntimeFrontierMode>("off");
  const [probeGpu, setProbeGpu] = useState(false);
  const [checkKey, setCheckKey] = useState(false);
  // Aktivasi ditolak karena belum siap; operator boleh menyimpan tetap secara sadar.
  const [canForce, setCanForce] = useState(false);
  const [check, setCheck] = useState<RuntimeCheck | null>(null);
  const [busy, setBusy] = useState<"check" | "activate" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  function adopt(next: RuntimeConfig) {
    setConfig(next);
    const effective = next.effective;
    setFrontierMode(
      next.local_only ? "off" : !next.state.frontier_mode
        ? effective.frontier.enabled
          ? (effective.frontier.mode as RuntimeFrontierMode)
          : "off"
        : next.state.frontier_mode,
    );
  }

  useEffect(() => {
    let cancelled = false;
    fetchRuntimeConfig()
      .then((next) => {
        if (!cancelled) adopt(next);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Pengaturan runtime gagal dimuat.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const body = () => ({ target: "local" as const });

  async function runCheck() {
    setBusy("check");
    setError(null);
    setNotice(null);
    try {
      setCheck(await checkRuntime({ ...body(), probe_gpu: probeGpu, check_frontier_key: checkKey }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Cek kesiapan gagal.");
    } finally {
      setBusy(null);
    }
  }

  async function activate(allowNotReady = false) {
    setBusy("activate");
    setError(null);
    setNotice(null);
    setCanForce(false);
    try {
      const next = await activateRuntime({ ...body(), frontier_mode: frontierMode, allow_not_ready: allowNotReady });
      adopt(next);
      setCheck(next.check);
      setNotice(
        (next.saved_not_ready
          ? `Konfigurasi TERSIMPAN tetapi BELUM SIAP (revisi ${next.state.revision}); run berikutnya bisa gagal sampai model tersedia.`
          : `Aktif untuk run berikutnya (revisi ${next.state.revision}).`) +
          (next.active_run ? " Run yang sedang berjalan tetap memakai konfigurasi lamanya." : ""),
      );
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        const detail = err.detail as { check?: RuntimeCheck; can_force?: boolean } | undefined;
        if (detail?.check) setCheck(detail.check);
        setCanForce(Boolean(detail?.can_force));
      }
      setError(err instanceof Error ? err.message : "Aktivasi gagal.");
    } finally {
      setBusy(null);
    }
  }

  const effective = config?.effective;
  const keyConfigured = !config?.local_only && (effective?.frontier.key_configured ?? false);
  return (
    <section className="dashboard__page runtime">
      <header className="reports__header">
        <div>
          <h2>Runtime & GPU</h2>
          <p className="dashboard__disclaimer">
            Periksa Ollama dan model pada perangkat Windows, Linux, atau macOS ini. Perubahan berlaku untuk run berikutnya; run
            yang sedang berjalan tidak ikut berubah. Key Sectors/DeepSeek tetap di <code>backend/.env</code> dan tidak
            bisa diisi dari sini.
          </p>
        </div>
      </header>

      {effective && config && (
        <div className="runtime__current">
          <h3>Berlaku sekarang (revisi {config.state.revision})</h3>
          <p className="event-card__source-meta">
            Perangkat ini (Ollama lokal) · {effective.ollama_url ?? "-"} · token{" "}
            {effective.auth === "token" ? `terpasang (${effective.token_fingerprint})` : "tidak ada"}
          </p>
          <p className="event-card__source-meta">
            Laporan: analis {effective.workflow.analyst ?? "-"} · pembanding{" "}
            {effective.workflow.reviewers.join(" → ") || "-"} · Screening: {effective.screening.analyst ?? "-"} →{" "}
            {effective.screening.reviewers.join(" → ") || "-"}
          </p>
          <p className="event-card__source-meta">
            Frontier: {effective.frontier.enabled ? effective.frontier.mode : "mati"} ·{" "}
            {effective.frontier.model_version ?? effective.frontier.model} · key{" "}
            {keyConfigured ? "terisi di backend" : "belum diisi di backend/.env"}
            {config.state.updated_at &&
              ` · diaktifkan ${new Date(config.state.updated_at).toLocaleString("id-ID", { dateStyle: "short", timeStyle: "short" })}`}
          </p>
          {config.state.ready_at_activation === false && (
            <p className="frontier__message">Konfigurasi ini disimpan walau belum siap saat diaktifkan.</p>
          )}
          {config.active_run && (
            <p className="dashboard__notice">Ada run berjalan ({config.active_run.kind}); ia tetap memakai konfigurasi saat dimulai.</p>
          )}
        </div>
      )}

      <form
        className="runtime__form"
        onSubmit={(event) => {
          event.preventDefault();
          void activate();
        }}
      >
        <p className="event-card__source-meta runtime__wide">
          Ollama lokal: {effective?.ollama_url ?? "http://127.0.0.1:11434"}. CPU tetap didukung; GPU mempercepat inferensi.
        </p>

        <label className="reports__field">
          <span>Reviewer frontier</span>
          <select value={frontierMode} onChange={(event) => setFrontierMode(event.target.value as RuntimeFrontierMode)}>
            <option value="off">mati</option>
            <option value="shadow" disabled={!keyConfigured}>
              shadow — hanya pembanding
            </option>
            <option value="escalation" disabled={!keyConfigured}>
              escalation — lewat aturan kode
            </option>
          </select>
        </label>
        {config?.local_only && <p className="event-card__source-meta runtime__wide">Inferensi lokal aktif. Frontier dinonaktifkan.</p>}
        {!keyConfigured && !config?.local_only && (
          <p className="event-card__source-meta runtime__wide">
            Shadow/escalation butuh <code>DEEPSEEK_API_KEY</code> di <code>backend/.env</code> lalu restart backend.
          </p>
        )}

        <label className="runtime__choice">
          <input type="checkbox" checked={probeGpu} onChange={(event) => setProbeGpu(event.target.checked)} />
          Buktikan GPU (memuat satu model sebentar, gratis)
        </label>
        <label className="runtime__choice">
          <input
            type="checkbox"
            checked={checkKey}
            disabled={!keyConfigured}
            onChange={(event) => setCheckKey(event.target.checked)}
          />
          Cek key DeepSeek dan saldo (tanpa generasi berbayar)
        </label>

        <div className="runtime__actions">
          <button type="button" className="dashboard__filter" disabled={busy !== null} onClick={() => void runCheck()}>
            {busy === "check" ? "Memeriksa…" : "Cek kesiapan"}
          </button>
          <button type="submit" className="dashboard__run-button" disabled={busy !== null}>
            {busy === "activate" ? "Mengaktifkan…" : "Aktifkan untuk run berikutnya"}
          </button>
        </div>
      </form>

      {error && (
        <div className="dashboard__error" role="alert">
          {error}
          {canForce && (
            <div>
              <button className="dashboard__filter" disabled={busy !== null} onClick={() => void activate(true)}>
                Simpan tetap walau belum siap
              </button>
            </div>
          )}
        </div>
      )}
      {notice && (
        <div className="dashboard__notice" role="status">
          {notice}
        </div>
      )}
      {check && (
        <section className="runtime__result" aria-live="polite">
          <h3>
            Hasil cek lokal ·{" "}
            {check.ready_for_next_run ? "siap untuk run berikutnya" : "belum siap"}
          </h3>
          <CheckView check={check} />
        </section>
      )}
    </section>
  );
}
