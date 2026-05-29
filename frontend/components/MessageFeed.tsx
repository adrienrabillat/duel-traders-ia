"use client";

import useSWR from "swr";
import { api, type Message } from "@/lib/api";

export function MessageFeed() {
  const { data, error, isLoading } = useSWR<Message[]>(
    "messages",
    () => api.getMessages(30),
    { refreshInterval: 10000 }
  );

  if (error) return null;
  if (isLoading || !data) {
    return <div className="card text-muted">Chargement des messages…</div>;
  }
  if (data.length === 0) {
    return (
      <div className="card text-muted text-sm">
        Aucun message public ou clan pour l'instant. La communication s'ouvre
        à partir de l'épisode 3.
      </div>
    );
  }

  return (
    <div className="card">
      <div className="text-xs text-muted uppercase tracking-wide mb-3">
        Messages publics
      </div>
      <ul className="space-y-3">
        {data.map((m) => (
          <li key={m.id} className="flex gap-3">
            <div className="w-9 h-9 rounded-full bg-ink text-white flex items-center justify-center text-sm font-semibold shrink-0">
              {m.sender_name[0]}
            </div>
            <div className="flex-1">
              <div className="flex items-center gap-2 text-sm">
                <span className="font-semibold">{m.sender_name}</span>
                <span className="text-xs text-muted">
                  Ép. {m.episode_number}
                  {m.channel === "clan" && " · clan"}
                </span>
              </div>
              <div className="text-sm text-ink/80 mt-0.5 whitespace-pre-wrap">
                {m.content}
              </div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
