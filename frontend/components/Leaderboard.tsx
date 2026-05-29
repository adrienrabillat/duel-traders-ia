"use client";

import useSWR from "swr";
import { api, type LeaderboardEntry, type Experiment } from "@/lib/api";

const fetcher = (key: "leaderboard" | "experiment") =>
  key === "leaderboard" ? api.getLeaderboard() : api.getExperiment();

export function Leaderboard() {
  const { data: entries, error, isLoading } = useSWR<LeaderboardEntry[]>(
    "leaderboard",
    () => api.getLeaderboard(),
    { refreshInterval: 5000 }
  );
  const { data: experiment } = useSWR<Experiment>(
    "experiment",
    () => api.getExperiment(),
    { refreshInterval: 30000 }
  );

  if (error) {
    return (
      <div className="card text-negative">
        Erreur API : {(error as Error).message}
      </div>
    );
  }
  if (isLoading || !entries) {
    return <div className="card text-muted">Chargement du leaderboard…</div>;
  }
  if (entries.length === 0) {
    return (
      <div className="card text-muted">
        Aucun candidat actif. Crée une expérience depuis l'admin pour démarrer.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {experiment && (
        <div className="card flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-xs text-muted uppercase tracking-wide">
              Expérience en cours
            </div>
            <div className="text-lg font-semibold">{experiment.name}</div>
          </div>
          <div className="flex gap-4 text-sm">
            <Stat label="Épisode" value={String(experiment.current_episode)} />
            <Stat label="Durée" value={`${experiment.duration_days}j`} />
            <Stat label="Statut" value={experiment.status} />
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {entries.map((e, idx) => (
          <CandidateCard key={e.candidate_id} entry={e} rank={idx + 1} />
        ))}
      </div>
    </div>
  );
}

function CandidateCard({
  entry,
  rank,
}: {
  entry: LeaderboardEntry;
  rank: number;
}) {
  const roi = parseFloat(entry.roi_pct);
  const value = parseFloat(entry.portfolio_value_usdc);
  const roiColor =
    roi > 0 ? "text-positive" : roi < 0 ? "text-negative" : "text-muted";

  return (
    <a href={`/c/${entry.candidate_id}`} className="card block hover:shadow-lg transition">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-xs text-muted">#{rank}</div>
          <div className="font-semibold text-lg">{entry.candidate_name}</div>
          <div className="text-xs text-muted truncate max-w-[180px]">
            {entry.model}
          </div>
        </div>
        <KindBadge kind={entry.kind} />
      </div>
      <div className="mt-4 flex items-end justify-between">
        <div>
          <div className="text-xs text-muted">Portefeuille</div>
          <div className="text-xl font-semibold tabular-nums">
            {value.toFixed(2)} <span className="text-xs text-muted">USDC</span>
          </div>
        </div>
        <div className={`text-right ${roiColor}`}>
          <div className="text-xs">ROI</div>
          <div className="text-lg font-semibold tabular-nums">
            {roi >= 0 ? "+" : ""}
            {roi.toFixed(2)}%
          </div>
        </div>
      </div>
    </a>
  );
}

function KindBadge({ kind }: { kind: string }) {
  const style: Record<string, string> = {
    individual: "bg-black/5 text-ink",
    team: "bg-accent/10 text-accent",
    ghost: "bg-muted/10 text-muted",
  };
  const label: Record<string, string> = {
    individual: "Solo",
    team: "Team",
    ghost: "HODL",
  };
  return (
    <span className={`pill ${style[kind] || "bg-black/5"}`}>
      {label[kind] || kind}
    </span>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="text-right">
      <div className="text-xs text-muted">{label}</div>
      <div className="font-semibold">{value}</div>
    </div>
  );
}
