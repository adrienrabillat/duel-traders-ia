"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { PendingOrders } from "@/components/PendingOrders";

export default function AdminPage() {
  const [killBusy, setKillBusy] = useState(false);
  const [killResult, setKillResult] = useState<string | null>(null);

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

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">Admin</h1>
          <p className="text-sm text-muted">
            Validation des ordres en attente, kill switch global.
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

      {killResult && (
        <div className="card text-sm">
          {killResult}
        </div>
      )}

      <section>
        <h2 className="text-lg font-semibold mb-3">Ordres en attente</h2>
        <PendingOrders />
      </section>
    </div>
  );
}
