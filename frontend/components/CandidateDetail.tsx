"use client";

import { useState } from "react";
import useSWR from "swr";
import {
  api,
  type Candidate,
  type Decision,
  type Strategy,
  type Trade,
} from "@/lib/api";
import { DecisionDrawer } from "./DecisionDrawer";

const fmtTime = (iso: string) =>
  new Date(iso).toLocaleString("fr-FR", {
    hour: "2-digit",
    minute: "2-digit",
    day: "2-digit",
    month: "short",
  });

const fmtNum = (v: string | number, decimals = 2) =>
  parseFloat(typeof v === "string" ? v : String(v)).toFixed(decimals);

export function CandidateDetail({ candidateId }: { candidateId: string }) {
  const [openDecision, setOpenDecision] = useState<Decision | null>(null);

  const { data: candidate, error: errCand } = useSWR<Candidate>(
    ["candidate", candidateId],
    () => api.getCandidate(candidateId),
    { refreshInterval: 10000 }
  );
  const { data: strategy } = useSWR<Strategy>(
    ["strategy", candidateId],
    () => api.getCandidateStrategy(candidateId),
    { refreshInterval: 30000, shouldRetryOnError: false }
  );
  const { data: decisions } = useSWR<Decision[]>(
    ["decisions", candidateId],
    () => api.getCandidateDecisions(candidateId, 50),
    { refreshInterval: 10000 }
  );
  const { data: trades } = useSWR<Trade[]>(
    ["trades", candidateId],
    () => api.getCandidateTrades(candidateId, 50),
    { refreshInterval: 10000 }
  );

  if (errCand) {
    return (
      <div className="card text-negative">
        Erreur : {(errCand as Error).message}
      </div>
    );
  }
  if (!candidate) {
    return <div className="card text-muted">Chargement…</div>;
  }

  const initialBalance = parseFloat(candidate.wallet.initial_balance);
  const usdcBalance = parseFloat(candidate.wallet.usdc_balance);
  const positionsValue = candidate.wallet.positions.reduce(
    (s, p) => s + parseFloat(p.quantity) * parseFloat(p.avg_cost_usdc),
    0
  );
  const portfolioValue = usdcBalance + positionsValue;
  const roiPct = ((portfolioValue - initialBalance) / initialBalance) * 100;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="card">
        <div className="flex items-start justify-between gap-4">
          <div>
            <a href="/" className="text-xs text-muted hover:underline">
              ← Retour au leaderboard
            </a>
            <h1 className="text-3xl font-bold mt-2">{candidate.name}</h1>
            <div className="text-sm text-muted mt-1">
              {candidate.model} · <KindLabel kind={candidate.kind} />
            </div>
          </div>
          <div className="text-right">
            <div className="text-xs text-muted">Portefeuille</div>
            <div className="text-2xl font-semibold tabular-nums">
              {fmtNum(portfolioValue)} <span className="text-sm text-muted">USDC</span>
            </div>
            <div
              className={`text-sm font-medium tabular-nums ${
                roiPct > 0 ? "text-positive" : roiPct < 0 ? "text-negative" : "text-muted"
              }`}
            >
              {roiPct >= 0 ? "+" : ""}
              {fmtNum(roiPct)}%
            </div>
          </div>
        </div>
      </div>

      {/* Portfolio breakdown */}
      <div className="card">
        <div className="text-xs text-muted uppercase tracking-wide mb-3">
          Composition du portefeuille
        </div>
        <div className="space-y-2 text-sm">
          <div className="flex justify-between border-b border-black/[0.04] pb-2">
            <span>USDC libre</span>
            <span className="font-medium tabular-nums">{fmtNum(usdcBalance)}</span>
          </div>
          {candidate.wallet.positions.length === 0 ? (
            <div className="text-muted text-sm py-2">Aucune position ouverte.</div>
          ) : (
            candidate.wallet.positions.map((p) => (
              <div
                key={p.symbol}
                className="flex justify-between border-b border-black/[0.04] pb-2"
              >
                <div>
                  <div className="font-medium">{p.symbol}</div>
                  <div className="text-xs text-muted">
                    {fmtNum(p.quantity, 6)} unités @ {fmtNum(p.avg_cost_usdc, 4)}
                  </div>
                </div>
                <div className="font-medium tabular-nums">
                  {fmtNum(parseFloat(p.quantity) * parseFloat(p.avg_cost_usdc))}
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Strategy */}
      <div className="card">
        <div className="text-xs text-muted uppercase tracking-wide mb-3">
          Stratégie courante
          {strategy && (
            <span className="ml-2 text-muted normal-case">
              · v{strategy.version} · ép. {strategy.episode_number}
            </span>
          )}
        </div>
        {strategy ? (
          <div className="text-sm whitespace-pre-wrap">{strategy.text}</div>
        ) : (
          <div className="text-muted text-sm">Stratégie pas encore définie par le modèle.</div>
        )}
      </div>

      {/* Decisions timeline */}
      <div className="card">
        <div className="text-xs text-muted uppercase tracking-wide mb-3">
          Décisions ({decisions?.length || 0})
        </div>
        {!decisions ? (
          <div className="text-muted text-sm">Chargement…</div>
        ) : decisions.length === 0 ? (
          <div className="text-muted text-sm">Aucune décision encore.</div>
        ) : (
          <ul className="divide-y divide-black/[0.04]">
            {decisions.map((d) => {
              const action = d.parsed_action as Record<string, unknown> | null;
              const kind = (action?.action as string) ?? "—";
              const summary = (action?.reasoning_summary as string) ?? "";
              return (
                <li key={d.id}>
                  <button
                    onClick={() => setOpenDecision(d)}
                    className="w-full text-left py-3 px-1 hover:bg-black/[0.02] rounded-lg transition flex gap-3"
                  >
                    <div className="text-xs text-muted shrink-0 w-24 tabular-nums">
                      {fmtTime(d.created_at)}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span
                          className={`pill ${kindStyle(kind)}`}
                        >
                          {kind}
                        </span>
                        <span className="text-xs text-muted">
                          {d.tokens_input}↑ {d.tokens_output}↓ tok · {d.latency_ms}ms
                        </span>
                      </div>
                      {summary && (
                        <div className="text-sm text-ink/70 mt-1 line-clamp-2">
                          {summary}
                        </div>
                      )}
                    </div>
                    <div className="text-muted shrink-0 self-center text-sm">›</div>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {/* Trades */}
      <div className="card">
        <div className="text-xs text-muted uppercase tracking-wide mb-3">
          Trades exécutés ({trades?.length || 0})
        </div>
        {!trades ? (
          <div className="text-muted text-sm">Chargement…</div>
        ) : trades.length === 0 ? (
          <div className="text-muted text-sm">Aucun trade exécuté.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-muted border-b border-black/[0.04]">
                  <th className="text-left py-2">Quand</th>
                  <th className="text-left py-2">Sens</th>
                  <th className="text-left py-2">Symbole</th>
                  <th className="text-right py-2">Qty</th>
                  <th className="text-right py-2">Prix</th>
                  <th className="text-right py-2">Notional</th>
                  <th className="text-right py-2">P&L</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-black/[0.04]">
                {trades.map((t) => {
                  const pnl = t.pnl_realized_usdc
                    ? parseFloat(t.pnl_realized_usdc)
                    : null;
                  return (
                    <tr key={t.id}>
                      <td className="py-2 text-xs text-muted">
                        {fmtTime(t.executed_at)}
                      </td>
                      <td className="py-2">
                        <span
                          className={`pill ${
                            t.side === "BUY"
                              ? "bg-positive/10 text-positive"
                              : "bg-negative/10 text-negative"
                          }`}
                        >
                          {t.side}
                        </span>
                      </td>
                      <td className="py-2 font-medium">{t.symbol}</td>
                      <td className="py-2 text-right tabular-nums">
                        {fmtNum(t.quantity, 6)}
                      </td>
                      <td className="py-2 text-right tabular-nums">
                        {fmtNum(t.price, 4)}
                      </td>
                      <td className="py-2 text-right tabular-nums">
                        {fmtNum(t.notional_usdc)}
                      </td>
                      <td
                        className={`py-2 text-right tabular-nums font-medium ${
                          pnl === null
                            ? "text-muted"
                            : pnl > 0
                            ? "text-positive"
                            : pnl < 0
                            ? "text-negative"
                            : "text-muted"
                        }`}
                      >
                        {pnl === null ? "—" : `${pnl >= 0 ? "+" : ""}${fmtNum(pnl)}`}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <DecisionDrawer
        decision={openDecision}
        onClose={() => setOpenDecision(null)}
      />
    </div>
  );
}

function KindLabel({ kind }: { kind: string }) {
  const labels: Record<string, string> = {
    individual: "Solo",
    team: "Team",
    ghost: "HODL fantôme",
  };
  return <>{labels[kind] || kind}</>;
}

function kindStyle(kind: string): string {
  switch (kind) {
    case "PLACE_ORDER":
      return "bg-positive/10 text-positive";
    case "CLOSE_POSITION":
      return "bg-negative/10 text-negative";
    case "UPDATE_STRATEGY":
      return "bg-accent/10 text-accent";
    case "SEND_MESSAGE":
      return "bg-blue-500/10 text-blue-700";
    case "QUERY_ORACLE":
      return "bg-purple-500/10 text-purple-700";
    case "HOLD":
      return "bg-black/5 text-muted";
    default:
      return "bg-black/5";
  }
}
