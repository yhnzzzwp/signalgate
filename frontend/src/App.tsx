import { useState } from "react";
import { Dashboard } from "./pages/Dashboard";
import { Reports } from "./pages/Reports";
import { Runtime } from "./pages/Runtime";

type Tab = "screening" | "reports" | "runtime";

const TABS: { key: Tab; label: string; hint: string }[] = [
  { key: "screening", label: "Screening aksi korporasi", hint: "Red flag struktural dari pengumuman dan berita" },
  { key: "reports", label: "Laporan emiten", hint: "Empat panel: fundamental, valuasi, technical, berita" },
  { key: "runtime", label: "Runtime & GPU", hint: "Ollama lokal, kesiapan model, CPU dan GPU" },
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
      {tab === "screening" ? <Dashboard /> : tab === "reports" ? <Reports /> : <Runtime />}
    </main>
  );
}

export default App;
