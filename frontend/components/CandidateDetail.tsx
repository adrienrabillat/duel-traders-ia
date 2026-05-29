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
import { TeamDeliberation } from "./TeamDeliberation";

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

      {/* Decisions / délibérations */}
      {candidate.kind === "team" ? (
        <div className="space-y-8">
          <div className="text-xs text-muted uppercase tracking-wide pl-1">
            Conversations du Conseil ({decisions?.length || 0})
          </div>
          {!decisions ? (
            <div className="card text-muted text-sm">Chargement…</div>
          ) : decisions.length === 0 ? (
            <div className="card text-muted text-sm">Aucune délibération encore.</div>
          ) : (
            <div className="space-y-12">
              {decisions.map((d, idx) => {
                const action = d.parsed_action as Record<string, unknown> | null;
                const kind = (action?.action as string) ?? "HOLD";
                return (
                  <div key={d.id} className="card">
                    <div className="flex items-center justify-between mb-4 pb-3 border-b border-black/[0.06]">
                      <div>
                        <div className="text-xs text-muted uppercase tracking-wide">
                          Conversation #{decisions.length - idx}
                        </div>
                        <div className="text-sm font-medium mt-1">
                          {fmtTime(d.created_at)}
                        </div>
                      </div>
                      <div className="text-right">
                        <div className="text-xs text-muted">Résultat</div>
                        <span className={`pill ${kindStyle(kind)} text-base mt-1 inline-block`}>
                          {kind}
                        </span>
                      </div>
                    </div>
                    <TeamDeliberation decisionId={d.id} />
                    <TeamOutcome decisionId={d.id} candidateId={candidate.id} />
                  </div>
                );
              })}
            </div>
          )}
        </div>
      ) : (
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
                          <span className={`pill ${kindStyle(kind)}`}>{kind}</span>
                          <span className="text-xs text-muted">
                            {d.tokens_input}↑ {d.tokens_output}↓ tok · {d.latency_ms}ms
                          </span>
                        </div>
                        {summary && (
                          <div className="text-sm text-ink/70 mt-1 line-clamp-2">{summary}</div>
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
      )}

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

function TeamOutcome({ decisionId, candidateId }: { decisionId: number; candidateId: number }) {
  const { data: deliberation } = useSWR(
    ["deliberation-outcome", decisionId],
    () => api.getDeliberation(decisionId),
    { refreshInterval: 0 }
  );
  const { data: trades } = useSWR(
    ["trades-for-outcome", candidateId],
    () => api.getCandidateTrades(String(candidateId), 50),
    { refreshInterval: 30000 }
  );

  if (!deliberation) return null;
  const finalKind = (deliberation.final_action?.action as string | undefined) ?? "HOLD";
  const action = deliberation.final_action as Record<string, unknown> | null;

  // Trade éventuellement issu de cette décision (1 PLACE_ORDER → 1 BUY trade)
  const tradesForThis = (trades || []).filter((t) => {
    const tradeAt = new Date(t.executed_at).getTime();
    const decisionAt = new Date(deliberation.created_at).getTime();
    return Math.abs(tradeAt - decisionAt) < 5 * 60 * 1000; // ±5 min
  });

  return (
    <div className="mt-6 pt-4 border-t border-black/[0.06]">
      <div className="text-xs text-muted uppercase tracking-wide mb-2">
        Conséquence de la délibération
      </div>
      {finalKind === "PLACE_ORDER" && action ? (
        <div className="text-sm space-y-1">
          <div>
            <span className="font-medium">{(action.side as string) || "BUY"}</span>{" "}
            sur <span className="font-medium">{action.symbol as string}</span> pour{" "}
            <span className="tabular-nums">{action.quote_amount_usdc as string} USDC</span>
            {" "}avec SL {action.stop_loss_pct as string}% / TP {action.take_profit_pct as string}%
          </div>
          {tradesForThis.length > 0 && (
            <div className="text-muted text-xs">
              ✅ Exécuté : {tradesForThis.map((t) => `${fmtNum(t.quantity, 6)} ${t.symbol} @ ${fmtNum(t.price, 2)}`).join(", ")}
            </div>
          )}
        </div>
      ) : finalKind === "CLOSE_POSITION" && action ? (
        <div className="text-sm">
          Fermeture position <span className="font-medium">{action.symbol as string}</span>
          {tradesForThis.length > 0 && (
            <div className="text-muted text-xs mt-1">
              ✅ Vendu : {tradesForThis.map((t) => `${fmtNum(t.quantity, 6)} ${t.symbol} @ ${fmtNum(t.price, 2)}`).join(", ")}
            </div>
          )}
        </div>
      ) : finalKind === "UPDATE_STRATEGY" ? (
        <div className="text-sm text-muted">📝 Stratégie mise à jour, aucun mouvement de portefeuille.</div>
      ) : finalKind === "QUERY_ORACLE" ? (
        <div className="text-sm text-muted">🔮 Oracle interrogé, prochain réveil dans ~1 min pour intégrer la réponse.</div>
      ) : finalKind === "SEND_MESSAGE" ? (
        <div className="text-sm text-muted">💬 Message envoyé aux autres candidats.</div>
      ) : (
        <div className="text-sm text-muted">⏸️ HOLD — aucun mouvement, prochain réveil planifié.</div>
      )}
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
