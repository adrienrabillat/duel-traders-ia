"use client";

import { useState } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import { PendingOrders } from "@/components/PendingOrders";

export default function AdminPage() {
  const [killBusy, setKillBusy] = useState(false);
  const [killResult, setKillResult] = useState<string | null>(null);
  const [wakeBusy, setWakeBusy] = useState<number | null>(null);
  const [wakeResults, setWakeResults] = useState<Record<number, string>>({});

  const { data: candidates } = useSWR("/api/candidates", () => api.getCandidates(), {
    refreshInterval: 30000,
  });

  const triggerKill = async () => {
    if (!confirm("KILL SWITCH : tous les ordres en attente seront rejetés et l'expérience stoppée. Confirmer ?")) {
      return;
    }
    setKillBusy(true);
    try {
      const result = await api.killSwitch();
      setKillResult(
        `Kill switch déclenché — ${result.pending_cancelled} ordre(s) annulé(s), expérience ${
          result.stopped ? "stoppée" : "déjà arrêtée"
        }.`
      );
    } catch (e) {
      setKillResult(`Erreur : ${(e as Error).message}`);
    } finally {
      setKillBusy(false);
    }
  };

  const triggerWake = async (candidateId: number, name: string) => {
    setWakeBusy(candidateId);
    try {
      const result = await api.forceWake(candidateId);
      const msg = result.ok
        ? `✅ ${result.candidate} : cycle ${result.cycle} terminé`
        : `❌ ${name} : ${result.error}`;
      setWakeResults((prev) => ({ ...prev, [candidateId]: msg }));
    } catch (e) {
      setWakeResults((prev) => ({
        ...prev,
        [candidateId]: `❌ ${name} : ${(e as Error).message}`,
      }));
    } finally {
      setWakeBusy(null);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">Admin</h1>
          <p className="text-sm text-muted">
            Validation des ordres, réveil forcé des candidats, kill switch.
          </p>
        </div>
        <button
          onClick={triggerKill}
          disabled={killBusy}
          className="btn-negative"
        >
          🛑 Kill switch
        </button>
      </div>

      {killResult && <div className="card text-sm">{killResult}</div>}

      <section>
        <h2 className="text-lg font-semibold mb-3">Force wake</h2>
        <p className="text-sm text-muted mb-3">
          Déclenche un cycle immédiat pour un candidat (bypass scheduler). En cas
          d&apos;erreur, le type d&apos;exception exact est renvoyé — utile pour
          diagnostiquer une clé API invalide ou un modèle indisponible.
        </p>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {candidates?.map((c) => (
            <div key={c.id} className="card">
              <div className="flex items-center justify-between gap-2 mb-2">
                <div>
                  <div className="font-semibold">{c.name}</div>
                  <div className="text-xs text-muted">{c.model}</div>
                </div>
                <button
                  onClick={() => triggerWake(c.id, c.name)}
                  disabled={wakeBusy === c.id}
                  className="btn-primary text-xs"
                >
                  {wakeBusy === c.id ? "..." : "Réveiller"}
                </button>
              </div>
              {wakeResults[c.id] && (
                <div className="text-xs mt-2 break-words">{wakeResults[c.id]}</div>
              )}
            </div>
          ))}
        </div>
      </section>

      <section>
        <h2 className="text-lg font-semibold mb-3">Ordres en attente</h2>
        <PendingOrders />
      </section>
    </div>
  );
}
