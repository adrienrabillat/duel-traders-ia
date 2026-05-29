"use client";

import { useEffect } from "react";
import { type Decision } from "@/lib/api";

const fmtTime = (iso: string) => new Date(iso).toLocaleString("fr-FR");

export function DecisionDrawer({
  decision,
  onClose,
}: {
  decision: Decision | null;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!decision) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [decision, onClose]);

  if (!decision) return null;

  const action = decision.parsed_action as Record<string, unknown> | null;
  const actionKind = (action?.action as string) ?? "INCONNU";

  return (
    <div className="fixed inset-0 z-50 flex">
      <div
        className="flex-1 bg-black/30 backdrop-blur-[2px]"
        onClick={onClose}
      />
      <div className="w-full sm:max-w-2xl h-full bg-card shadow-2xl overflow-y-auto">
        <div className="sticky top-0 bg-card border-b border-black/[0.06] px-5 py-4 flex items-center justify-between">
          <div>
            <div className="text-xs text-muted">
              Épisode {decision.episode_number} · {fmtTime(decision.created_at)}
            </div>
            <div className="font-semibold text-lg flex items-center gap-2 mt-0.5">
              {actionKind}
              <span className="pill bg-black/5">{decision.provider}</span>
            </div>
          </div>
          <button onClick={onClose} className="btn-ghost text-2xl leading-none">
            ×
          </button>
        </div>

        <div className="p-5 space-y-5">
          <Section title="Métadonnées">
            <div className="grid grid-cols-2 gap-3 text-sm">
              <Stat label="Modèle" value={decision.model} />
              <Stat label="Latence" value={`${decision.latency_ms} ms`} />
              <Stat label="Tokens in" value={String(decision.tokens_input)} />
              <Stat label="Tokens out" value={String(decision.tokens_output)} />
              <Stat label="Tentatives" value={String(decision.attempt_count)} />
            </div>
          </Section>

          {decision.parse_error && (
            <Section title="Erreur de parsing">
              <div className="text-negative text-sm whitespace-pre-wrap">
                {decision.parse_error}
              </div>
            </Section>
          )}

          {action && (
            <Section title="Action décidée">
              <pre className="bg-surface rounded-xl p-3 text-xs overflow-x-auto whitespace-pre-wrap">
                {JSON.stringify(action, null, 2)}
              </pre>
            </Section>
          )}

          <Section title="Réponse brute du modèle">
            <pre className="bg-surface rounded-xl p-3 text-xs overflow-x-auto whitespace-pre-wrap">
              {decision.raw_response}
            </pre>
          </Section>
        </div>
      </div>
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="text-xs text-muted uppercase tracking-wide mb-2">{title}</div>
      {children}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-xs text-muted">{label}</span>
      <span className="font-medium truncate">{value}</span>
    </div>
  );
}
