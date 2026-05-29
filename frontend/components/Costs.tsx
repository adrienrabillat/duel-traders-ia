"use client";

import useSWR from "swr";
import { api, type CostSummary } from "@/lib/api";

const fmtUsd = (raw: string | number): string => {
  const n = typeof raw === "string" ? parseFloat(raw) : raw;
  if (n < 0.01) return `$${n.toFixed(5)}`;
  if (n < 1) return `$${n.toFixed(4)}`;
  return `$${n.toFixed(2)}`;
};

const fmtTokens = (n: number): string => {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
};

export function Costs() {
  const { data, error, isLoading } = useSWR<CostSummary>(
    "costs",
    () => api.getCosts(),
    { refreshInterval: 10000 }
  );

  if (error) return null;
  if (isLoading || !data) {
    return <div className="card text-muted">Chargement des coûts…</div>;
  }
  if (data.entries.length === 0) {
    return (
      <div className="card text-muted text-sm">
        Aucun appel LLM enregistré pour l'instant.
      </div>
    );
  }

  return (
    <div className="card">
      <div className="flex items-baseline justify-between mb-4">
        <div>
          <div className="text-xs text-muted uppercase tracking-wide">
            Coûts API LLM cumulés
          </div>
          <div className="text-2xl font-semibold tabular-nums mt-1">
            {fmtUsd(data.total_cost_usd)}
          </div>
        </div>
        <div className="text-right text-xs text-muted">
          <div>{data.total_calls} appels</div>
          <div>
            {fmtTokens(data.total_tokens_input)} in ·{" "}
            {fmtTokens(data.total_tokens_output)} out
          </div>
        </div>
      </div>
      <div className="space-y-2">
        {data.entries.map((e) => (
          <CostRow key={e.candidate_id} entry={e} />
        ))}
      </div>
    </div>
  );
}

function CostRow({ entry }: { entry: CostSummary["entries"][number] }) {
  return (
    <div className="flex items-center justify-between py-2 border-b last:border-b-0 border-black/[0.04]">
      <div className="min-w-0">
        <div className="font-medium truncate">{entry.candidate_name}</div>
        <div className="text-xs text-muted truncate">{entry.model}</div>
      </div>
      <div className="flex items-center gap-6 text-sm">
        <div className="text-right">
          <div className="text-xs text-muted">Appels</div>
          <div className="tabular-nums">{entry.call_count}</div>
        </div>
        <div className="text-right">
          <div className="text-xs text-muted">Tokens</div>
          <div className="tabular-nums text-xs">
            {fmtTokens(entry.total_tokens_input)} /{" "}
            {fmtTokens(entry.total_tokens_output)}
          </div>
        </div>
        <div className="text-right min-w-[80px]">
          <div className="text-xs text-muted">Coût</div>
          <div className="font-semibold tabular-nums">
            {fmtUsd(entry.total_cost_usd)}
          </div>
        </div>
      </div>
    </div>
  );
}
