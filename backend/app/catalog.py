"""Katalog sinyal: satu sumber kebenaran untuk spesifikasi produk dan mesin penilaian.

Spesifikasi yang hidup di dokumen terpisah akan menyimpang dari kode, dan ketika itu terjadi tidak
ada yang tahu mana yang benar. Katalog ini dapat dieksekusi: `tests/test_catalog.py` menolak setiap
aturan yang ada di kode tetapi tidak di sini, dan sebaliknya. Dokumen spesifikasi merujuk ke sini,
bukan menyalin isinya.

Setiap entri menjawab empat pertanyaan yang diminta Tahap 1:
- **data wajib** — tanpa ini sinyal tidak boleh dinyatakan, bukan ditebak
- **aturan** — syarat deterministik, dihitung Python, bukan oleh model
- **pengecualian** — keadaan yang membuat aturan sengaja diam
- **alasan** — mengapa ini bermakna bagi pembaca, bukan sekadar korelasi
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Side = Literal["red", "growth"]
Origin = Literal["fact", "market"]
# Tiga keadaan, bukan dua. "implemented" saja menyembunyikan perbedaan yang penting: aturan yang
# sudah berjalan pada data nyata versus aturan yang lulus unit test tetapi belum pernah dijalankan
# sekali pun terhadap data sungguhan. Empat sinyal data pasar berada di keadaan kedua karena
# membutuhkan data Sectors, sementara mode hemat API masih aktif.
Status = Literal["verified", "untested", "planned"]

SECTORS_BLOCKED = ("Butuh data Sectors; mode hemat API aktif sehingga belum pernah dijalankan "
                   "pada data nyata.")


@dataclass(frozen=True)
class SignalSpec:
    code: str
    name: str
    side: Side
    weight: int
    origin: Origin
    required_data: tuple[str, ...]
    rule: str
    exceptions: tuple[str, ...]
    rationale: str
    status: Status
    example: str = ""
    # Diisi hanya untuk status "untested": apa yang masih perlu terjadi sebelum boleh disebut teruji.
    blocked_by: str = ""


# --------------------------------------------------------------------- fakta dari dokumen sumber
# Diekstrak model lokal, diverifikasi Python terhadap kutipan, lalu diperiksa pembanding independen.
# Model tidak pernah menentukan bobot maupun label.

FACT_SIGNALS: tuple[SignalSpec, ...] = (
    SignalSpec(
        code="business_change", name="Pergantian atau penambahan bidang usaha", side="red", weight=3,
        origin="fact",
        status="verified",
        required_data=("kutipan verbatim dari dokumen sumber", "bidang usaha lama dan baru disebut"),
        rule="Fakta `business_change=present` terverifikasi kutipannya dan didukung pembanding.",
        exceptions=(
            "Perluasan di dalam bisnis inti bukan pergantian bidang usaha.",
            "Rencana yang masih berupa wacana tanpa agenda RUPS tidak dihitung.",
        ),
        rationale="Bobot tertinggi karena inilah inti pola backdoor listing: cangkang lama dipakai "
                  "untuk bisnis yang sama sekali berbeda.",
        example="FORU: dari media ke pertambangan, disertai rights issue Rp2,7 T.",
    ),
    SignalSpec(
        code="old_business", name="Bisnis atau anak usaha lama dilepas", side="red", weight=2,
        origin="fact",
        status="verified",
        required_data=("kutipan verbatim", "identitas entitas yang dilepas"),
        rule="Fakta `old_business_divested=present` terverifikasi.",
        exceptions=("Pelepasan aset non-inti yang tidak mengubah sumber pendapatan utama.",),
        rationale="Dilepasnya bisnis lama berbarengan dengan masuknya bisnis baru memperkuat dugaan "
                  "pergantian cangkang, bukan ekspansi.",
    ),
    SignalSpec(
        code="asset_injection", name="Injeksi aset dari pihak terkait", side="red", weight=2,
        origin="fact",
        status="verified",
        required_data=("kutipan verbatim", "nama pihak penyetor aset", "bentuk aset atau piutang"),
        rule="Fakta `asset_injection=present` terverifikasi.",
        exceptions=("Pembelian aset dari pihak ketiga tanpa hubungan afiliasi.",),
        rationale="Aset yang disuntikkan pihak terkait dinilai oleh pihak yang berkepentingan atas "
                  "nilainya; ini titik paling rawan pada transaksi afiliasi.",
        example="MGLV: Rp668,61 miliar untuk mengambil alih piutang PT Nextier Datamate Center.",
    ),
    SignalSpec(
        code="counterparty_new", name="Penerima saham atau dana adalah pihak baru", side="red", weight=2,
        origin="fact",
        status="verified",
        required_data=("nama pihak yang spesifik", "kutipan yang memuat nama itu"),
        rule="Fakta `counterparty=new_party` terverifikasi dan namanya ada di kutipan yang sama.",
        exceptions=(
            "Nama generik seperti 'investor' atau 'pemegang saham utama' ditolak, bukan diterima.",
            "Emiten sendiri tidak dihitung sebagai penerima eksternal.",
        ),
        rationale="Pihak baru yang masuk lewat penerbitan saham non-HMETD adalah cara paling umum "
                  "kendali berpindah tanpa penawaran tender.",
    ),
    SignalSpec(
        code="counterparty_known", name="Penerima adalah pemegang saham lama atau afiliasinya",
        side="growth", weight=2, origin="fact",
        status="verified",
        required_data=("nama pihak yang spesifik", "kutipan yang memuat nama itu"),
        rule="Fakta `counterparty=existing_shareholder` atau `counterparty=affiliate` terverifikasi.",
        exceptions=("Snapshot kepemilikan saat ini tidak membuktikan siapa pengendali sebelum transaksi.",),
        rationale="Pemegang saham lama yang menambah modal menanggung dilusi yang sama dengan yang "
                  "lain; kepentingannya sejajar dengan pemegang saham publik.",
    ),
    SignalSpec(
        code="counterparty_public", name="Saham ditawarkan ke seluruh pemegang saham", side="growth",
        weight=1, origin="fact",
        status="untested",
        blocked_by="Belum ada kasus HMETD murni pada 25 kasus yang tersimpan.",
        required_data=("kutipan yang menyebut HMETD atau penawaran proporsional",),
        rule="Fakta `counterparty=public` terverifikasi.",
        exceptions=("Pembeli siaga yang menyerap sisa dapat mengubah efek proporsionalnya.",),
        rationale="HMETD memberi kesempatan setiap pemegang saham mempertahankan porsinya, jadi "
                  "dilusinya pilihan, bukan paksaan.",
    ),
    SignalSpec(
        code="funds_core", name="Dana untuk ekspansi bisnis inti", side="growth", weight=2, origin="fact",
        status="verified",
        required_data=("kutipan yang menyebut penggunaan dana", "kaitan ke bisnis yang sedang berjalan"),
        rule="Fakta `use_of_funds=core_expansion` terverifikasi.",
        exceptions=("Klaim ekspansi yang tidak tercermin pada pendapatan memicu sinyal kontradiksi.",),
        rationale="Modal yang masuk ke bisnis yang sudah terbukti berjalan punya dasar penilaian; "
                  "modal ke bisnis yang belum ada tidak.",
    ),
    SignalSpec(
        code="funds_working", name="Dana untuk modal kerja", side="growth", weight=1, origin="fact",
        status="verified",
        required_data=("kutipan yang menyebut modal kerja",),
        rule="Fakta `use_of_funds=working_capital` terverifikasi.",
        exceptions=("Modal kerja berulang di tengah arus kas operasi negatif bukan pertumbuhan.",),
        rationale="Bobot rendah karena modal kerja netral: bisa menopang pertumbuhan, bisa menambal "
                  "kebocoran.",
    ),
    SignalSpec(
        code="funds_new", name="Dana untuk bisnis baru di luar bisnis inti", side="red", weight=1,
        origin="fact",
        status="verified",
        required_data=("kutipan yang menyebut bisnis di luar bidang usaha berjalan",),
        rule="Fakta `use_of_funds=new_business` terverifikasi.",
        exceptions=("Diversifikasi ke rantai nilai yang sama masih terhitung bisnis inti.",),
        rationale="Dana publik yang masuk ke bisnis tanpa rekam jejak tidak punya dasar penilaian.",
    ),
    SignalSpec(
        code="funds_debt_only", name="Dana hanya untuk melunasi utang", side="red", weight=2,
        origin="fact", status="untested",
        blocked_by="`debt_repayment` baru diberi aturan skor; belum muncul pada 25 kasus tersimpan.",
        required_data=("kutipan yang menyebut pelunasan utang",
                       "tidak ada kutipan penggunaan dana lain yang membangun bisnis"),
        rule="Ada `use_of_funds=debt_repayment` terverifikasi dan tidak ada `core_expansion`, "
             "`working_capital`, maupun `acquisition` yang terverifikasi.",
        exceptions=(
            "Melunasi utang sambil berekspansi adalah cerita berbeda dan tidak menyalakan sinyal.",
            "Restrukturisasi yang menukar utang menjadi saham tercatat sebagai injeksi aset, "
            "bukan pelunasan.",
        ),
        rationale="Melunasi utang dengan ekuitas baru memindahkan risiko dari kreditur ke pemegang "
                  "saham publik tanpa menambah kemampuan emiten menghasilkan kas. Yang dinilai "
                  "eksklusivitasnya, bukan keberadaan pelunasannya.",
        example="Rights issue yang seluruh dananya dipakai melunasi pinjaman bank, tanpa belanja modal.",
    ),
)

# ----------------------------------------------------------- data pasar, dihitung penuh oleh Python
# Tidak pernah melibatkan model. Semuanya berbobot 1 sehingga tidak bisa mencapai ambang label
# sendirian: data pasar memberi konteks pada aksi korporasinya, bukan menilai sahamnya.

MARKET_SIGNALS: tuple[SignalSpec, ...] = (
    SignalSpec(
        code="valuation_gap", name="Valuasi jauh di atas sebayanya", side="red", weight=1, origin="market",
        status="untested",
        blocked_by=SECTORS_BLOCKED,
        required_data=("PBV emiten", "PB agregat subsektor tahun terakhir"),
        rule="PBV emiten lebih dari 3x PB subsektornya. Bila data subsektor tidak tersedia, dipakai "
             "ambang mutlak 20x.",
        exceptions=(
            "PB subsektor nol atau negatif membuat perbandingannya diabaikan, bukan dipaksakan.",
            "Valuasi tinggi yang sejalan dengan sebayanya tidak menyalakan sinyal.",
        ),
        rationale="Ambang tunggal untuk seluruh pasar praktis tidak pernah menyala di sektor "
                  "bervaluasi rendah; perbandingan hanya bermakna terhadap sebayanya.",
        example="PB subsektor banks 2026 = 0,80x, jadi bank dengan PBV 4,2x setara 5,2x sebayanya "
                "sementara ambang 20x tidak pernah tersentuh.",
    ),
    SignalSpec(
        code="float_risk", name="Free float tipis saat emiten menambah modal", side="red", weight=1,
        origin="market",
        status="untested",
        blocked_by=SECTORS_BLOCKED,
        required_data=("free float emiten", "bucket aksi korporasi"),
        rule="Free float di bawah 15% dan bucket termasuk rights_issue atau non_preemptive_capital.",
        exceptions=("Di luar bucket penggalangan dana, free float tipis bukan sinyal aksi korporasi.",),
        rationale="Pada float tipis, penerbitan saham baru memusatkan kendali alih-alih menyebarkannya, "
                  "dan harga mudah digerakkan volume kecil.",
        example="Subsektor banks: 15 dari 48 emiten punya free float di bawah 15%, terendah 7,49%.",
    ),
    SignalSpec(
        code="cash_burn", name="Menggalang dana di tengah arus kas operasi negatif", side="red", weight=1,
        origin="market",
        status="untested",
        blocked_by=SECTORS_BLOCKED,
        required_data=("arus kas operasi 4 kuartal berurutan", "bucket aksi korporasi"),
        rule="Arus kas operasi negatif pada minimal 3 dari 4 kuartal terakhir saat emiten menggalang dana.",
        exceptions=("Satu kuartal tanpa angka membuat aturan diam, bukan menebak dengan data sebagian.",),
        rationale="Satu kuartal lemah biasa; menggalang dana sementara operasi terus menguras kas adalah "
                  "pola yang berbeda.",
    ),
    SignalSpec(
        code="expansion_contradiction", name="Klaim ekspansi tidak tercermin pada pendapatan", side="red",
        weight=1, origin="market",
        status="untested",
        blocked_by=SECTORS_BLOCKED,
        required_data=("fakta use_of_funds=core_expansion", "pendapatan 4 kuartal berurutan"),
        rule="Ada klaim ekspansi bisnis inti sementara pendapatan turun tanpa putus sepanjang 4 kuartal.",
        exceptions=(
            "Pendapatan kuartalan musiman, jadi penurunan yang terputus satu kuartal saja tidak dihitung.",
        ),
        rationale="Klaim penggunaan dana harus bertahan saat dihadapkan pada angka yang dilaporkan "
                  "emiten sendiri.",
    ),
    SignalSpec(
        code="controller_exit", name="Pengendali melepas saham saat publik diminta menyerap",
        side="red", weight=2, origin="market", status="untested",
        blocked_by=SECTORS_BLOCKED,
        required_data=("laporan keterbukaan kepemilikan IDX untuk emiten yang sama",
                       "arah transaksi dan persentase yang dilepas", "bucket aksi korporasi"),
        rule="Penjualan kumulatif oleh pemegang berstatus insider atau investor korporasi mencapai "
             "CONTROLLER_EXIT_MIN_PERCENT persen, dan bucket termasuk penggalangan dana.",
        exceptions=(
            "Penjualan di luar periode penggalangan dana bukan pola exit liquidity.",
            "Pembelian oleh insider pada periode yang sama tidak mengurangi hitungan penjualan; "
            "keduanya dilaporkan terpisah agar tidak saling menutupi.",
        ),
        rationale="Pihak yang paling tahu kondisi emiten mengurangi posisinya justru ketika dana "
                  "publik diminta masuk. Dihitung dari laporan keterbukaan, bukan dari teks berita.",
        example="'Diskon Gede, Pengendali Jual Puluhan Juta Saham MGLV' -- pola yang perlu "
                "diperiksa terhadap laporan keterbukaannya, bukan diterima dari judulnya.",
    ),
)

# --------------------------------------------------------------- belum dapat dinyatakan sama sekali
# Absennya sesuatu tidak sama dengan tidak adanya sesuatu. Sinyal di bawah membutuhkan dokumen yang
# memang akan menyebutkannya bila ada, plus field ekstraksi yang belum ada.

PLANNED_SIGNALS: tuple[SignalSpec, ...] = (
    SignalSpec(
        code="no_standby_buyer", name="Rights issue tanpa pembeli siaga", side="red", weight=1,
        origin="fact", status="planned",
        blocked_by="Butuh field ekstraksi `standby_buyer` dan dokumen keterbukaan/prospektus yang "
                   "memang menyebutkannya bila ada. Dari artikel berita, 'tidak disebut' tidak "
                   "dapat dibedakan dari 'tidak ada'.",
        required_data=("dokumen keterbukaan informasi atau prospektus, bukan artikel berita",
                       "field ekstraksi yang menyatakan ada/tidak ada pembeli siaga beserta namanya"),
        rule="Belum ditetapkan. Kandidat: dokumen yang lazim menyebut pembeli siaga tidak menyebut "
             "satu pun, pada rights issue yang porsi publiknya besar.",
        exceptions=(
            "Ketiadaan pembeli siaga bisa berarti pemegang saham lama sudah berkomitmen menyerap "
            "seluruhnya -- itu justru sinyal sebaliknya, jadi aturannya tidak boleh sepihak.",
        ),
        rationale="Tanpa pembeli siaga, porsi yang tidak terserap membatalkan sebagian rencana, dan "
                  "beban dilusi jatuh pada yang ikut. Tetapi menyimpulkannya dari artikel berita "
                  "akan menghasilkan red flag palsu setiap kali wartawan tidak menyebutkannya.",
        example="Belum ada; menunggu field ekstraksi dan sumber dokumen yang tepat.",
    ),
)

SIGNALS: tuple[SignalSpec, ...] = FACT_SIGNALS + MARKET_SIGNALS + PLANNED_SIGNALS

# Ambang label. Data pasar seluruhnya berbobot 1 sedangkan ambang merah 4, jadi tanpa fakta
# terverifikasi dari dokumen sumber tidak ada label yang bisa terbit.
RED_FLAG_THRESHOLD = 4
GROWTH_THRESHOLD = 3


@dataclass(frozen=True)
class OutputLabel:
    key: str
    name: str
    definition: str
    requires: tuple[str, ...]
    example: str
    status: Status
    # Kepada siapa label berlaku. `holding` berarti hanya bermakna bagi pemegang posisi, sehingga
    # tidak dapat terbit sebagai rekomendasi umum.
    scope: Literal["general", "holding"] = "general"
    blocks: tuple[str, ...] = field(default_factory=tuple)


# ------------------------------------------------- label penyaringan: menilai aksi korporasinya
SCREENING_LABELS: tuple[OutputLabel, ...] = (
    OutputLabel(
        key="structural_red_flag", name="Red Flag Struktural", status="verified",
        definition="Pola aksi korporasi cocok dengan injeksi aset atau pergantian cangkang usaha.",
        requires=(
            f"total bobot merah minimal {RED_FLAG_THRESHOLD}",
            "bobot merah minimal 2 lebih tinggi daripada bobot pertumbuhan",
            "seluruh fakta yang dipakai berstatus didukung pembanding",
        ),
        example="Pergantian bidang usaha (3) + injeksi aset (2) + dana ke bisnis baru (1) = 6 merah "
                "melawan 0 pertumbuhan.",
    ),
    OutputLabel(
        key="growth_catalyst", name="Katalis Pertumbuhan", status="verified",
        definition="Penggalangan dana yang penerima dan penggunaannya sejalan dengan bisnis berjalan.",
        requires=(
            f"total bobot pertumbuhan minimal {GROWTH_THRESHOLD}",
            "bobot merah tidak lebih dari 1",
            "harus ada fakta ekspansi bisnis inti",
        ),
        example="Penerima pemegang saham lama (2) + dana untuk ekspansi inti (2) = 4 pertumbuhan "
                "melawan 0 merah.",
    ),
    OutputLabel(
        key="inconclusive", name="Belum Simpulan", status="verified",
        definition="Bukti belum cukup, atau pembaca independen tidak menyetujui pembacaan analis.",
        requires=("keadaan bawaan bila tidak ada ambang lain terpenuhi",),
        example="MGLV: pertumbuhan 3 melawan merah 2 — tidak satu pun ambang tercapai, dan pembanding "
                "tidak menyetujui label draft.",
    ),
)

# ------------------------------- label rekomendasi: menilai sahamnya. BELUM diterapkan (Tahap 6).
# Gate saat ini justru menolak bahasa transaksi. Perubahannya menunggu metode valuasi Tahap 6,
# bukan sekadar penambahan label.

# ============================================================ keputusan dan siklus hidupnya
# Dua sumbu yang terpisah. Menyatukannya membuat "sedang ditinjau" tampak seperti penilaian atas
# saham, padahal itu keadaan sebuah rekomendasi yang sudah terbit. Keputusan menjawab "apa
# kesimpulannya"; siklus hidup menjawab "apakah kesimpulan itu masih berlaku".
#
# `sell` sengaja tidak ada. Menyarankan jual kepada yang tidak memegang saham bukan saran keluar
# melainkan ajakan short, dan itu produk berbeda dengan kewajiban berbeda. Pencabutan berarti
# rekomendasi pembelian tidak lagi berlaku; bukan instruksi menjual.

DECISIONS: tuple[OutputLabel, ...] = (
    OutputLabel(
        key="buy", name="Buy", status="planned",
        definition="Harga acuan berada di bawah nilai wajar hasil perhitungan dengan selisih minimal "
                   "MARGIN_OF_SAFETY, pada horizon yang dinyatakan.",
        requires=(
            "metode valuasi yang terdaftar untuk kelompok bisnis emiten",
            "seluruh data wajib metode itu tersedia dan tidak lebih tua dari satu periode pelaporan",
            "harga acuan beserta tanggal dan jamnya",
            "(nilai wajar - harga acuan) / nilai wajar >= MARGIN_OF_SAFETY",
            "horizon dan tanggal evaluasi",
            "minimal satu kondisi pembatalan yang dapat diuji secara otomatis",
            "tidak ada aksi korporasi berstatus red flag yang belum selesai",
            "versi aturan penilaian yang dipakai",
        ),
        example="Belum ada; menunggu metode valuasi Tahap 6.",
        blocks=("Gate saat ini menolak kata 'buy'; pelonggarannya bagian dari Tahap 6.",),
    ),
    OutputLabel(
        key="no_recommendation", name="Belum Ada Rekomendasi", status="planned",
        definition="Keadaan bawaan. Syarat BUY belum terpenuhi, termasuk ketika data lengkap tetapi "
                   "selisih harga terhadap nilai wajar belum memenuhi ambang. Tidak ada rekomendasi yang "
                   "diterbitkan. Ini bukan `hold` dan bukan penilaian netral atas sahamnya.",
        requires=("selalu tersedia; dipakai setiap kali syarat buy tidak terpenuhi",),
        example="Data kuartalan lebih tua dari satu periode pelaporan, metode valuasi belum terdaftar, "
                "atau selisih harga terhadap nilai wajar belum mencapai ambang margin of safety.",
    ),
)


@dataclass(frozen=True)
class LifecycleState:
    key: str
    name: str
    definition: str
    terminal: bool
    reasons: tuple[str, ...]


# Hanya rekomendasi BUY yang punya siklus hidup. `no_recommendation` tidak menerbitkan apa pun,
# jadi tidak ada yang bisa ditangguhkan, dicabut, atau kedaluwarsa.
LIFECYCLE_STATES: tuple[LifecycleState, ...] = (
    LifecycleState(
        key="active", name="Berlaku", terminal=False,
        definition="Dalam horizon dan asumsi materialnya belum berubah.",
        reasons=("diterbitkan setelah seluruh syarat buy terpenuhi",
                 "tinjauan selesai dan asumsinya masih berlaku"),
    ),
    LifecycleState(
        key="suspended", name="Ditangguhkan", terminal=False,
        definition="Asumsi material berubah dan belum dinilai ulang. Rekomendasi berhenti berlaku "
                   "sementara, tetapi belum dicabut karena dasarnya mungkin masih utuh.",
        reasons=(
            "aksi korporasi yang menjadi dasar ditunda",
            "laporan keuangan yang dipakai direvisi emiten",
            "informasi material baru terbit dan belum dinilai",
            "data wajib metode valuasi menjadi kedaluwarsa",
        ),
    ),
    LifecycleState(
        key="revoked", name="Dicabut", terminal=True,
        definition="Dasarnya tidak lagi berlaku sebelum horizon berakhir. Rekomendasi ditarik dan "
                   "tidak dapat dihidupkan kembali; penilaian baru menerbitkan rekomendasi baru.",
        reasons=(
            "aksi korporasi yang menjadi dasar dibatalkan",
            "salah satu kondisi pembatalan yang dinyatakan terpenuhi",
            "asumsi valuasi terbukti salah oleh data yang lebih baru",
            "red flag struktural terbit untuk emiten yang sama",
        ),
    ),
    LifecycleState(
        key="expired", name="Kedaluwarsa", terminal=True,
        definition="Horizon berakhir tanpa diperbarui. Bukan pernyataan benar atau salah, melainkan "
                   "bahwa masa berlakunya habis.",
        reasons=("tanggal akhir horizon terlampaui tanpa penilaian ulang",),
    ),
)

# Yang wajib tersimpan pada setiap rekomendasi BUY, sekarang dan selamanya sesudahnya. Tanpa ini
# rekomendasi lama tidak dapat dievaluasi benar atau salahnya, dan pilot bertanggal Tahap 8 kehilangan
# dasarnya. Riwayat tidak pernah dihapus; perubahan status menambah baris, bukan menimpa.
RETAINED_FIELDS: tuple[str, ...] = (
    "decision",
    "issued_at",
    "reference_price",
    "reference_price_at",
    "fair_value",
    "margin",
    "horizon_end",
    "review_due",
    "evidence_case_ids",
    "evidence_fact_ids",
    "rule_version",
    "catalog_version",
)

# Selisih minimum antara harga acuan dan nilai wajar sebelum rekomendasi boleh terbit. Angkanya
# ditetapkan bersama metode valuasi di Tahap 6 dan diversikan bersama aturan penilaian; dicantumkan
# di sini supaya syaratnya dapat dihitung, bukan dinilai dengan kalimat "melebihi ketidakpastian".
MARGIN_OF_SAFETY: float | None = None



def by_code(code: str) -> SignalSpec | None:
    return next((signal for signal in SIGNALS if signal.code == code), None)


def implemented_codes() -> set[str]:
    return {signal.code for signal in SIGNALS if signal.status == "implemented"}
