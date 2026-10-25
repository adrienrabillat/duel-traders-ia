"use client";

import { useState } from "react";
import useSWR, { mutate } from "swr";
import { api, type Order } from "@/lib/api";

export function PendingOrders() {
  const { data: orders, error, isLoading } = useSWR<Order[]>(
    "pending-orders",
    () => api.getPendingOrders(),
    { refreshInterval: 3000 }
  );

  if (error) {
    return (
      <div className="card text-negative">
        Erreur API : {(error as Error).message}
        <div className="text-xs text-muted mt-2">
          Vérifie que ADMIN_API_TOKEN est défini côté serveur si l'auth est active.
        </div>
      </div>
    );
  }
  if (isLoading || !orders) {
    return <div className="card text-muted">Chargement…</div>;
  }
  if (orders.length === 0) {
    return (
      <div className="card text-muted text-center py-10">
        Pas d'ordre en attente. Tu peux respirer.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {orders.map((o) => (
        <PendingOrderCard key={o.id} order={o} />
      ))}
    </div>
  );
}

function PendingOrderCard({ order }: { order: Order }) {
  const [busy, setBusy] = useState(false);
  const [reason, setReason] = useState("");
  const [askReject, setAskReject] = useState(false);

  const approve = async () => {
    setBusy(true);
    try {
      await api.approveOrder(order.id);
      mutate("pending-orders");
    } catch (e) {
      alert(`Approve failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const reject = async () => {
    setBusy(true);
    try {
      await api.rejectOrder(order.id, reason);
      mutate("pending-orders");
    } catch (e) {
      alert(`Reject failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const amount = parseFloat(order.quote_amount_usdc);
  const sl = order.stop_loss_pct ? parseFloat(order.stop_loss_pct) : null;
  const tp = order.take_profit_pct ? parseFloat(order.take_profit_pct) : null;
  const ageS = Math.floor(
    (Date.now() - new Date(order.created_at).getTime()) / 1000
  );

  return (
    <div className="card">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <span
              className={`pill ${
                order.side === "BUY"
                  ? "bg-positive/10 text-positive"
                  : "bg-negative/10 text-negative"
              }`}
            >
              {order.side}
            </span>
            <span className="font-semibold text-lg">{order.symbol}</span>
            <span className="text-xs text-muted">
              Candidat #{order.candidate_id}
            </span>
          </div>
          <div className="mt-2 text-sm text-muted">
            Notional : <span className="text-ink font-medium">{amount.toFixed(2)} USDC</span>
            {sl !== null && (
              <>
                {" · "}SL : <span className="text-ink">-{sl}%</span>
              </>
            )}
            {tp !== null && (
              <>
                {" · "}TP : <span className="text-ink">+{tp}%</span>
              </>
            )}
          </div>
          <div className="mt-1 text-xs text-muted">
            En attente depuis {ageS}s · Auto-approve dans {Math.max(0, 120 - ageS)}s
          </div>
        </div>
        <div className="flex gap-2">
          {askReject ? (
            <>
              <input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Raison…"
                className="px-3 py-1 rounded-xl border text-sm"
              />
              <button onClick={reject} disabled={busy} className="btn-negative">
                Confirmer
              </button>
              <button
                onClick={() => setAskReject(false)}
                className="btn-ghost"
              >
                Annuler
              </button>
            </>
          ) : (
            <>
              <button onClick={approve} disabled={busy} className="btn-positive">
                Approuver
              </button>
              <button
                onClick={() => setAskReject(true)}
                disabled={busy}
                className="btn-ghost"
              >
                Rejeter
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
