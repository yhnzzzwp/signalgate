"""Reviewer frontier opsional (DeepSeek) di atas model lokal.

Frontier tidak pernah menentukan label, skor, atau status terbit. Ia membaca bukti terpilih secara
independen; hasilnya disimpan sebagai pembanding (mode shadow) atau dipakai lewat aturan rekonsiliasi
kode yang eksplisit (mode escalation). Kegagalan frontier tidak pernah menaikkan hasil menjadi supported.
"""
