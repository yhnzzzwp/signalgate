import type { ActionBucket, VerdictLabel } from "./types";

/**
 * Seluruh teks berbahasa Indonesia untuk nilai enum backend, di satu tempat.
 *
 * Backend memakai kunci bahasa Inggris karena itu yang tersimpan di database dan audit trail;
 * penerjemahannya terjadi di sini saja, jadi menambah label baru tidak perlu menyisir komponen.
 */

export const LABEL_TEXT: Record<VerdictLabel, string> = {
  growth_catalyst: "Katalis Pertumbuhan",
  structural_red_flag: "Red Flag Struktural",
  inconclusive: "Belum Simpulan",
};

export const BUCKET_TEXT: Record<ActionBucket, string> = {
  control_change: "Perubahan Pengendali",
  non_preemptive_capital: "Modal Tanpa HMETD",
  rights_issue: "Rights Issue",
  general_action: "Aksi Korporasi",
};

export const STAGE_TEXT: Record<string, string> = {
  scan: "pindai sumber",
  sense: "kumpulkan kandidat",
  research: "riset",
  validate: "validasi",
  gate: "kepatuhan",
};

export const RESEARCH_STATUS_TEXT: Record<string, string> = {
  completed: "selesai",
  needs_review: "perlu pemeriksaan",
  inconclusive: "belum simpulan",
  insufficient_evidence: "bukti tidak cukup",
  needs_document: "menunggu dokumen",
  missing_sectors_data: "data emiten tidak tersedia",
  model_unavailable: "model tidak aktif",
  deterministic_only: "hanya sinyal deterministik",
};

export const GATE_STATUS_TEXT: Record<string, string> = {
  passed: "lolos",
  needs_review: "perlu pemeriksaan",
};

export const FACT_TOPIC_TEXT: Record<string, string> = {
  counterparty: "Penerima saham/dana",
  use_of_funds: "Penggunaan dana",
  business_change: "Pergantian bidang usaha",
  old_business_divested: "Bisnis lama dilepas",
  asset_injection: "Injeksi aset",
};

export const VALIDATOR_STATUS_TEXT: Record<string, string> = {
  supported: "didukung pembanding",
  not_supported: "tidak didukung",
  contradicted: "dibantah pembanding",
  unknown: "belum diperiksa",
};

export const FRONTIER_STATUS_TEXT: Record<string, string> = {
  disabled: "tidak aktif",
  not_triggered: "tidak dipicu (tidak ada konflik)",
  completed: "selesai",
  partial: "sebagian (langkah kedua tidak berjalan)",
  unavailable: "tidak tersedia",
  failed: "gagal",
  budget_exhausted: "batas budget tercapai",
  offline_cache_miss: "offline: respons tersimpan tidak ada",
  in_flight: "pekerjaan sama sedang berjalan",
};

export const FRONTIER_MODE_TEXT: Record<string, string> = {
  shadow: "shadow — hanya pembanding, tidak memengaruhi hasil",
  escalation: "escalation — dipakai lewat aturan rekonsiliasi kode",
};

export const FRONTIER_TRIGGER_TEXT: Record<string, string> = {
  reviewer_conflict: "pembanding lokal berbeda pendapat",
  timeline_conflict: "angka lintas waktu belum terselesaikan",
  label_conflict: "pembanding berbeda soal label",
};

export const FRONTIER_RULE_TEXT: Record<string, string> = {
  R0_mechanical: "gagal cek mekanis — frontier tidak bisa menganulir",
  R1_frontier_missing: "frontier tanpa putusan sah — status lokal tetap",
  R2_frontier_unstable: "frontier berubah pendapat — tidak dipakai",
  R3_tie_break: "frontier memutus konflik pembanding lokal",
  R3_no_match: "frontier berbeda dari semua pembanding — konflik tetap",
  R4_downgrade: "diturunkan ke belum terverifikasi",
  R5_agree: "sepakat dengan lokal",
  R5_keep_local: "status lokal dipertahankan",
};

export const TIMELINE_STAGE_TEXT: Record<string, string> = {
  plan: "rencana",
  revision: "revisi",
  approval: "persetujuan",
  realization: "realisasi",
  unknown: "tahap tidak jelas",
};

export const TIMELINE_METRIC_TEXT: Record<string, string> = {
  shares: "jumlah saham",
  value_idr: "nilai (Rp)",
  percentage: "persentase",
  price_idr: "harga per saham",
};
