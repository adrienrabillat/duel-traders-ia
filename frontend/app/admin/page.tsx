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

  const triggerWake = async (candidateId: number, name: string, turns = 1) => {
    setWakeBusy(candidateId);
    try {
      const result = await api.forceWake(candidateId, turns);
      const lines = result.runs.map((r, i) =>
        r.ok
          ? `✅ tour ${i + 1} : cycle ${r.cycle} terminé`
          : `❌ tour ${i + 1} : ${r.error}`
      );
      setWakeResults((prev) => ({ ...prev, [candidateId]: lines.join("\n") }));
    } catch (e) {
      setWakeResults((prev) => ({
        ...prev,
        [candidateId]: `❌ ${name} : ${(e as Error).message}`,
      }));
    } finally {
      setWakeBusy(null);
    }
  };

  const triggerReset = async (candidateId: number, name: string) => {
    if (
      !confirm(
        `RESET ${name} : on efface toutes ses décisions, délibérations, ordres et trades, ` +
          `et on remet son wallet à 10 USDC. L'historique est perdu. Confirmer ?`
      )
    ) {
      return;
    }
    setWakeBusy(candidateId);
    try {
      const r = await api.resetCandidate(candidateId);
      setWakeResults((prev) => ({
        ...prev,
        [candidateId]:
          `🔄 ${r.candidate} reset — ${r.cleared.decisions} décisions, ` +
          `${r.cleared.trades} trades, ${r.cleared.orders} ordres effacés. ` +
          `Wallet : ${r.wallet.usdc_balance} USDC.`,
      }));
    } catch (e) {
      setWakeResults((prev) => ({
        ...prev,
        [candidateId]: `❌ Reset ${name} : ${(e as Error).message}`,
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
                <div className="flex flex-col gap-1">
                  <button
                    onClick={() => triggerWake(c.id, c.name, 1)}
                    disabled={wakeBusy === c.id}
                    className="btn-primary text-xs"
                  >
                    {wakeBusy === c.id ? "..." : "Réveiller"}
                  </button>
                  {c.kind === "team" && (
                    <>
                      <button
                        onClick={() => triggerWake(c.id, c.name, 3)}
                        disabled={wakeBusy === c.id}
                        className="btn-primary text-xs"
                        title="Enchaîne 3 délibérations d'affilée"
                      >
                        × 3 tours
                      </button>
                      <button
                        onClick={() => triggerReset(c.id, c.name)}
                        disabled={wakeBusy === c.id}
                        className="btn-negative text-xs"
                      >
                        🔄 Reset
                      </button>
                    </>
                  )}
                </div>
              </div>
              {wakeResults[c.id] && (
                <pre className="text-xs mt-2 whitespace-pre-wrap break-words">
                  {wakeResults[c.id]}
                </pre>
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
