/**
 * Client API pour le backend FastAPI.
 *
 * Toutes les fonctions utilisent fetch + JSON. SWR est utilisé côté composant
 * pour le polling et la revalidation automatique.
 */

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

function adminHeaders(): HeadersInit {
  const token = process.env.NEXT_PUBLIC_ADMIN_TOKEN;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init.headers || {}),
    },
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText} — ${body}`);
  }
  return (await res.json()) as T;
}

// =============================================================================
// Types
// =============================================================================

export type Experiment = {
  id: number;
  name: string;
  started_at: string;
  ended_at: string | null;
  status: string;
  duration_days: number;
  current_episode: number;
};

export type Position = {
  symbol: string;
  quantity: string;
  avg_cost_usdc: string;
};

export type Wallet = {
  usdc_balance: string;
  initial_balance: string;
  positions: Position[];
};

export type Candidate = {
  id: number;
  name: string;
  model: string;
  kind: string;
  is_active: boolean;
  wallet: Wallet;
};

export type LeaderboardEntry = {
  candidate_id: number;
  candidate_name: string;
  model: string;
  kind: string;
  portfolio_value_usdc: string;
  roi_pct: string;
  realized_pnl_usdc: string;
};

export type Order = {
  id: number;
  candidate_id: number;
  side: string;
  symbol: string;
  quote_amount_usdc: string;
  stop_loss_pct: string | null;
  take_profit_pct: string | null;
  status: string;
  decision_price: string | null;
  fill_price: string | null;
  fill_quantity: string | null;
  fee_usdc: string | null;
  error_message: string | null;
  created_at: string;
  validated_at: string | null;
  filled_at: string | null;
  closed_at: string | null;
};

export type Message = {
  id: number;
  sender_name: string;
  channel: string;
  recipients: string[];
  content: string;
  episode_number: number;
  sent_at: string;
};

export type CostSummaryEntry = {
  candidate_id: number;
  candidate_name: string;
  model: string;
  provider: string;
  call_count: number;
  total_tokens_input: number;
  total_tokens_output: number;
  total_cost_usd: string;
  last_call_at: string | null;
};

export type CostSummary = {
  entries: CostSummaryEntry[];
  total_cost_usd: string;
  total_calls: number;
  total_tokens_input: number;
  total_tokens_output: number;
};

// =============================================================================
// Public
// =============================================================================

export type Trade = {
  id: number;
  side: string;
  symbol: string;
  quantity: string;
  price: string;
  notional_usdc: string;
  fee_usdc: string;
  pnl_realized_usdc: string | null;
  executed_at: string;
};

export type Strategy = {
  version: number;
  text: string;
  episode_number: number;
  created_at: string;
};

export type Decision = {
  id: number;
  candidate_id: number;
  episode_number: number;
  raw_response: string;
  parsed_action: Record<string, unknown> | null;
  parse_error: string | null;
  provider: string;
  model: string;
  tokens_input: number;
  tokens_output: number;
  latency_ms: number;
  attempt_count: number;
  created_at: string;
};

export type DeliberationTurn = {
  id: number;
  turn_number: number;
  speaker_name: string;
  speaker_model: string;
  speaker_provider: string;
  message_to_team: string;
  proposed_action: Record<string, unknown> | null;
  proposed_action_kind: string | null;
  parse_error: string | null;
  tokens_input: number;
  tokens_output: number;
  latency_ms: number;
  created_at: string;
};

export type Deliberation = {
  decision_id: number;
  episode_number: number;
  created_at: string;
  final_action: Record<string, unknown> | null;
  turns: DeliberationTurn[];
  vote_counts: Record<string, number>;
};

export const api = {
  getExperiment: () => request<Experiment>("/api/experiment"),
  getCandidates: () => request<Candidate[]>("/api/candidates"),
  getCandidate: (id: number | string) => request<Candidate>(`/api/candidates/${id}`),
  getCandidateStrategy: (id: number | string) =>
    request<Strategy>(`/api/candidates/${id}/strategy`),
  getCandidateDecisions: (id: number | string, limit = 30) =>
    request<Decision[]>(`/api/candidates/${id}/decisions?limit=${limit}`),
  getCandidateTrades: (id: number | string, limit = 50) =>
    request<Trade[]>(`/api/candidates/${id}/trades?limit=${limit}`),
  getDeliberation: (decisionId: number | string) =>
    request<Deliberation>(`/api/decisions/${decisionId}/deliberations`),
  getLeaderboard: () => request<LeaderboardEntry[]>("/api/leaderboard"),
  getRecentOrders: (limit = 30) =>
    request<Order[]>(`/api/orders/recent?limit=${limit}`),
  getMessages: (limit = 30) => request<Message[]>(`/api/messages?limit=${limit}`),
  getCosts: () => request<CostSummary>("/api/costs"),

  // Admin
  getPendingOrders: () =>
    request<Order[]>("/admin/orders/pending", { headers: adminHeaders() }),
  approveOrder: (orderId: number) =>
    request<Order>(`/admin/orders/${orderId}/approve`, {
      method: "POST",
      headers: adminHeaders(),
    }),
  rejectOrder: (orderId: number, reason = "") =>
    request<Order>(`/admin/orders/${orderId}/reject`, {
      method: "POST",
      headers: adminHeaders(),
      body: JSON.stringify({ reason }),
    }),
  killSwitch: () =>
    request<{ stopped: boolean; pending_cancelled: number }>(
      "/admin/kill-switch",
      { method: "POST", headers: adminHeaders() }
    ),
  forceWake: (candidateId: number, turns = 1) =>
    request<{
      ok: boolean;
      runs: Array<{ ok: boolean; candidate?: string; cycle?: string; error?: string }>;
    }>(
      `/admin/candidates/${candidateId}/wake?turns=${turns}`,
      { method: "POST", headers: adminHeaders() }
    ),
  resetCandidate: (candidateId: number) =>
    request<{
      ok: boolean;
      candidate: string;
      cleared: { decisions: number; trades: number; orders: number; strategies: number };
      wallet: { usdc_balance: string };
    }>(`/admin/candidates/${candidateId}/reset`, {
      method: "POST",
      headers: adminHeaders(),
    }),
};
