import { useState } from "react";
import { Dashboard } from "./pages/Dashboard";
import { Reports } from "./pages/Reports";

type Tab = "screening" | "reports";

const TABS: { key: Tab; label: string; hint: string }[] = [
  { key: "screening", label: "Screening aksi korporasi", hint: "Red flag struktural dari pengumuman dan berita" },
  { key: "reports", label: "Laporan emiten", hint: "Empat panel: fundamental, valuasi, technical, berita" },
];

export function App() {
  const [tab, setTab] = useState<Tab>("screening");
  return (
    <main className="dashboard">
      <nav className="app__tabs" aria-label="Bagian aplikasi">
        {TABS.map((item) => (
          <button
            key={item.key}
            className={`app__tab ${tab === item.key ? "app__tab--active" : ""}`}
            aria-pressed={tab === item.key}
            title={item.hint}
            onClick={() => setTab(item.key)}
          >
            {item.label}
          </button>
        ))}
      </nav>
      {tab === "screening" ? <Dashboard /> : <Reports />}
    </main>
  );
}

export default App;
