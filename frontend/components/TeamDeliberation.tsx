"use client";

import useSWR from "swr";
import { api, type Deliberation, type DeliberationTurn } from "@/lib/api";

const fmtTime = (iso: string) =>
  new Date(iso).toLocaleString("fr-FR", {
    hour: "2-digit",
    minute: "2-digit",
    day: "2-digit",
    month: "short",
  });

const SPEAKER_COLORS: Record<string, { bg: string; text: string; avatar: string }> = {
  Claude: { bg: "bg-orange-500/10", text: "text-orange-700", avatar: "bg-orange-500" },
  ChatGPT: { bg: "bg-emerald-500/10", text: "text-emerald-700", avatar: "bg-emerald-500" },
  Mistral: { bg: "bg-rose-500/10", text: "text-rose-700", avatar: "bg-rose-500" },
  Gemini: { bg: "bg-blue-500/10", text: "text-blue-700", avatar: "bg-blue-500" },
  Grok: { bg: "bg-zinc-700/10", text: "text-zinc-800", avatar: "bg-zinc-700" },
};

const DEFAULT_COLOR = {
  bg: "bg-black/5",
  text: "text-ink",
  avatar: "bg-ink",
};

function kindStyle(kind: string | null): string {
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
      return "bg-black/5 text-muted";
  }
}

export function TeamDeliberation({ decisionId }: { decisionId: number }) {
  const { data, error, isLoading } = useSWR<Deliberation>(
    ["deliberation", decisionId],
    () => api.getDeliberation(decisionId),
    { refreshInterval: 0 }
  );

  if (error) return <div className="text-negative text-sm">Erreur : {(error as Error).message}</div>;
  if (isLoading || !data) return <div className="text-muted text-sm">Chargement…</div>;

  const finalKind = (data.final_action?.action as string | undefined) ?? null;
  const turnNumbers = Array.from(new Set(data.turns.map((t) => t.turn_number))).sort();

  return (
    <div className="space-y-5">
      {/* Header avec verdict */}
      <div className="card bg-surface">
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div>
            <div className="text-xs text-muted uppercase tracking-wide">
              Délibération · Épisode {data.episode_number} · {fmtTime(data.created_at)}
            </div>
            <div className="mt-1 text-lg font-semibold">
              Verdict :{" "}
              <span className={`pill ${kindStyle(finalKind)} text-base`}>
                {finalKind || "HOLD"}
              </span>
            </div>
          </div>
          <div className="text-right">
            <div className="text-xs text-muted">Votes</div>
            <div className="flex gap-1 flex-wrap justify-end mt-1">
              {Object.entries(data.vote_counts).map(([kind, count]) => (
                <span key={kind} className={`pill ${kindStyle(kind)} text-xs`}>
                  {kind} · {count}
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Fil de discussion */}
      {turnNumbers.map((turn) => (
        <div key={turn} className="space-y-3">
          <div className="text-xs text-muted uppercase tracking-wide pl-1">
            Tour {turn}
          </div>
          <div className="space-y-3">
            {data.turns
              .filter((t) => t.turn_number === turn)
              .map((t) => (
                <ChatBubble turn={t} key={t.id} />
              ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function ChatBubble({ turn }: { turn: DeliberationTurn }) {
  const color = SPEAKER_COLORS[turn.speaker_name] || DEFAULT_COLOR;
  return (
    <div className="flex gap-3">
      <div
        className={`${color.avatar} w-9 h-9 rounded-full flex items-center justify-center text-white font-semibold shrink-0`}
      >
        {turn.speaker_name[0]}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1 flex-wrap">
          <span className={`font-semibold ${color.text}`}>{turn.speaker_name}</span>
          <span className="text-xs text-muted">{turn.speaker_model}</span>
          {turn.proposed_action_kind && (
            <span className={`pill ${kindStyle(turn.proposed_action_kind)} text-xs`}>
              propose {turn.proposed_action_kind}
            </span>
          )}
          {turn.parse_error && (
            <span className="pill bg-negative/10 text-negative text-xs">
              parse error
            </span>
          )}
        </div>
        <div className={`${color.bg} rounded-2xl rounded-tl-md px-4 py-3 text-sm whitespace-pre-wrap`}>
          {turn.message_to_team || "(message vide)"}
        </div>
        <div className="text-xs text-muted mt-1 pl-1">
          {turn.tokens_input}↑ {turn.tokens_output}↓ tokens · {turn.latency_ms} ms
        </div>
      </div>
    </div>
  );
}
