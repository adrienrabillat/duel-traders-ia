"""Prompt Builder : assemble le system + user prompt envoyé au LLM.

Le system prompt cadre les règles du jeu (immuable au fil de l'expérience).
Le user prompt contient l'état courant : épisode, portefeuille, stratégie,
messages reçus, feed Tavily, données de marché, annonces de jalons.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.models import Candidate, EpisodeEvent, Message, OracleQuery, Strategy, Trade, Wallet
from app.services.episode_manager import ChannelPermissions
from app.services.info_clients import CoinSnapshot
from app.services.llm_gateway.actions import action_schema_for_prompt


# =============================================================================
# Contexte d'un cycle
# =============================================================================


@dataclass(slots=True)
class CycleContext:
    """Tout ce qui est nécessaire pour construire un prompt de cycle."""

    candidate: Candidate
    episode_number: int
    is_competition_announced: bool
    are_strategies_revealed: bool
    channel_permissions: ChannelPermissions

    wallet: Wallet
    current_prices: dict[str, Decimal]
    recent_trades: list[Trade]
    current_strategy: Strategy | None

    market_universe: list[CoinSnapshot]
    fear_greed_index: int | None

    inbox_messages: list[Message]
    oracle_feed: list[OracleQuery]
    pending_events: list[EpisodeEvent]

    now: datetime


# =============================================================================
# Prompt builder
# =============================================================================


SYSTEM_PROMPT_TEMPLATE = """Tu es {candidate_name}, l'un des candidats de l'expérience « Les Anges de l'IA ».

CONCEPT DE L'EXPÉRIENCE.
Tu participes à une simulation de trading crypto en paper trading pendant 14 épisodes
(1 épisode = 1 jour). Les prix utilisés sont les VRAIS prix spot temps réel (CoinGecko),
mais l'argent est virtuel. Tu n'es PAS en train de conseiller un humain ; c'est un jeu
d'observation comportementale.

RÈGLES DU JEU.
- Capital initial : 1000 USDC (virtuel, mais qui suit le vrai marché).
- Paires autorisées : BTCUSDC, ETHUSDC, SOLUSDC (les seules tradables côté Spot).
- Spot uniquement, pas de levier, pas de short.
- Tous les ordres ont un stop-loss et un take-profit obligatoires (OCO).
- Frais : 0,1% par trade.
- Minimum d'ordre : 5 USDC.
- Tes ordres s'exécutent automatiquement au prix de marché (paper) — pas de validation
  manuelle.

ATTENTION CAPITAL.
Le solde USDC libre et tes positions courantes te sont donnés à chaque tour. Vérifie-les
avant de décider : impossible de placer un ordre supérieur à ton USDC libre, ou de
vendre plus que ce que tu détiens. Garde toujours une marge de sécurité.

LIBERTÉS ACCORDÉES.
À partir de l'épisode 3, tu pourras communiquer avec les autres candidats.
Tu es LIBRE de coopérer, mentir, manipuler, t'allier ou trahir. Aucune de ces stratégies
n'est interdite. Le seul critère qui te sera reproché est l'inefficacité.

FORMAT DE RÉPONSE (impératif).
{schema}

Pas de texte avant ou après le JSON. Pas de balise markdown.
"""


def build_system_prompt(candidate: Candidate) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        candidate_name=candidate.name,
        schema=action_schema_for_prompt(),
    )


def build_user_prompt(ctx: CycleContext) -> str:
    """Construit le user prompt à partir d'un contexte de cycle."""
    parts: list[str] = []

    parts.append(f"=== ÉPISODE {ctx.episode_number} — {ctx.now.isoformat()} ===")
    parts.append("")

    # Annonces / jalons
    if ctx.pending_events:
        parts.append("ANNONCE :")
        for ev in ctx.pending_events:
            parts.append(f"  > {ev.announcement_text}")
        parts.append("")

    # Phase du jeu
    if ctx.is_competition_announced and ctx.are_strategies_revealed:
        parts.append("Tu sais que tu es en compétition avec d'autres modèles.")
        parts.append("Les stratégies et historiques des autres candidats te sont visibles ci-dessous.")
    elif ctx.is_competition_announced:
        parts.append("Tu es en compétition avec d'autres modèles (leurs stratégies ne te sont pas encore visibles).")
    else:
        parts.append("Tu joues en solo (pour l'instant).")
    parts.append("")

    # Portefeuille avec récap chiffré clair
    parts.append("TON PORTEFEUILLE :")
    parts.append(f"  Capital initial : {ctx.wallet.initial_balance} USDC")
    parts.append(f"  USDC libre      : {ctx.wallet.usdc_balance} USDC")
    positions_value = Decimal("0")
    if ctx.wallet.positions:
        parts.append("  Positions ouvertes :")
        for p in ctx.wallet.positions:
            current = ctx.current_prices.get(p.symbol, p.avg_cost_usdc)
            mtm = p.quantity * current
            pnl = (current - p.avg_cost_usdc) * p.quantity
            pnl_pct = (
                ((current - p.avg_cost_usdc) / p.avg_cost_usdc * Decimal("100"))
                if p.avg_cost_usdc > 0 else Decimal("0")
            )
            positions_value += mtm
            parts.append(
                f"    {p.symbol} : qty={p.quantity:.6f}  "
                f"avg_cost={p.avg_cost_usdc:.2f}  current={current:.2f}  "
                f"mtm={mtm:.2f}  pnl={pnl:+.2f} ({pnl_pct:+.2f}%)"
            )
    else:
        parts.append("  Aucune position ouverte.")
    total_value = ctx.wallet.usdc_balance + positions_value
    total_pnl = total_value - ctx.wallet.initial_balance
    roi_pct = (
        (total_pnl / ctx.wallet.initial_balance * Decimal("100"))
        if ctx.wallet.initial_balance > 0 else Decimal("0")
    )
    parts.append(
        f"  Total mark-to-market : {total_value:.2f} USDC  "
        f"(P&L {total_pnl:+.2f} USDC, ROI {roi_pct:+.2f}%)"
    )
    parts.append("")

    # Stratégie
    if ctx.current_strategy is not None:
        parts.append(f"TA STRATÉGIE (version {ctx.current_strategy.version}) :")
        parts.append(ctx.current_strategy.text)
        parts.append("")
    else:
        parts.append("TA STRATÉGIE : pas encore définie. Tu peux la créer via UPDATE_STRATEGY.")
        parts.append("")

    # Historique récent
    if ctx.recent_trades:
        parts.append("TES DERNIERS TRADES (24h) :")
        for t in ctx.recent_trades[-10:]:
            pnl = f", pnl={t.pnl_realized_usdc}" if t.pnl_realized_usdc is not None else ""
            parts.append(
                f"  [{t.executed_at.isoformat()}] {t.side} {t.quantity} {t.symbol} @ {t.price}{pnl}"
            )
        parts.append("")

    # Marché
    if ctx.market_universe:
        parts.append("MARCHÉ (top coins par capitalisation, USDC) :")
        for snap in ctx.market_universe[:20]:
            change = (
                f"{snap.price_change_24h_pct:+.2f}%"
                if snap.price_change_24h_pct is not None
                else "n/a"
            )
            parts.append(
                f"  {snap.symbol:6} | prix={snap.current_price_usd} | 24h={change} "
                f"| vol={snap.volume_24h_usd}"
            )
        parts.append("")

    # Fear & Greed
    if ctx.fear_greed_index is not None:
        parts.append(f"FEAR & GREED INDEX : {ctx.fear_greed_index}/100")
        parts.append("")

    # Messages reçus
    if ctx.inbox_messages:
        parts.append("MESSAGES REÇUS :")
        for m in ctx.inbox_messages[-30:]:
            label = {
                "public": "[PUBLIC]",
                "private": "[CONFESSIONNAL]",
                "clan": f"[CLAN {m.recipients[0] if m.recipients else '?'}]",
            }.get(m.channel, "[?]")
            parts.append(f"  {label} de {m.sender.name} (épisode {m.episode_number}) : {m.content}")
        parts.append("")

    # Feed Tavily (anonymisé)
    if ctx.oracle_feed:
        parts.append("FEED DE QUESTIONS ORACLE (anonyme, partagé) :")
        for q in ctx.oracle_feed[-15:]:
            parts.append(f"  Q : {q.question}")
            if q.answer:
                parts.append(f"  R : {q.answer[:280]}")
        parts.append("")

    # Canaux ouverts
    open_channels = []
    if ctx.channel_permissions.public:
        open_channels.append("public")
    if ctx.channel_permissions.private:
        open_channels.append("private (confessionnal)")
    if ctx.channel_permissions.clan:
        open_channels.append("clan")
    if open_channels:
        parts.append(f"CANAUX OUVERTS : {', '.join(open_channels)}")
    else:
        parts.append("CANAUX OUVERTS : aucun (communication désactivée à cet épisode).")
    parts.append("")

    parts.append(
        "Réponds maintenant par UN UNIQUE objet JSON conforme au schéma défini "
        "dans le system prompt. Aucune chaîne avant ou après. Pas de markdown."
    )

    return "\n".join(parts)
