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
