"""Prompt Builder : assemble le system + user prompt envoyé au LLM.

Le system prompt cadre les règles du jeu (immuable au fil de l'expérience).
Le user prompt contient l'état courant : épisode, portefeuille, stratégie,
messages reçus, feed Tavily, données de marché, annonces de jalons.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from app.models import Candidate, EpisodeEvent, Message, OracleQuery, Strategy, Trade, Wallet
from app.services.episode_manager import ChannelPermissions
from app.services.info_clients import CoinSnapshot
from app.services.llm_gateway.actions import action_schema_for_prompt


# =============================================================================
# Catégories tradables — chaque candidat doit en choisir UNE comme thèse
# d'investissement. Il ne peut ensuite trader que dans cette catégorie.
# =============================================================================


CATEGORY_TO_SYMBOLS: dict[str, list[str]] = {
    "MAJORS": ["BTC", "ETH"],
    "L1": ["SOL", "ADA", "AVAX", "DOT", "ATOM", "NEAR", "APT", "SUI", "TON"],
    "L2": ["MATIC", "POL", "OP", "ARB", "STRK", "ZK"],
    "DEFI": ["LINK", "UNI", "AAVE", "MKR", "CRV", "SNX", "PENDLE", "LDO"],
    "MEMECOINS": ["DOGE", "SHIB", "PEPE", "FLOKI", "WIF", "BONK"],
    "AI": ["FET", "RNDR", "TAO", "AGIX", "OCEAN", "WLD"],
    "GAMING": ["SAND", "MANA", "GALA", "IMX", "AXS", "RON"],
    "PRIVACY_RWA": ["XMR", "ZEC", "ONDO", "TIA", "INJ"],
}

CATEGORY_DESCRIPTIONS: dict[str, str] = {
    "MAJORS": "Les deux blue chips. Sécurité, liquidité maximale, volatilité modérée.",
    "L1": "Les concurrents de Ethereum. Plus de volatilité, narratives écosystèmes.",
    "L2": "Solutions de scalabilité sur Ethereum. Cycliques, sensibles à l'activité L1.",
    "DEFI": "Tokens de gouvernance et utility des protocoles DeFi.",
    "MEMECOINS": "Pure spéculation sur les narratives. Volatilité extrême, x10 ou -90%.",
    "AI": "Tokens liés aux narratives IA / compute décentralisé.",
    "GAMING": "Tokens des jeux et metaverses Web3.",
    "PRIVACY_RWA": "Privacy coins et Real World Assets. Plus thématiques que techniques.",
}


def coin_category(coin_symbol: str) -> str | None:
    """Renvoie la catégorie d'un coin, ou None si non répertorié."""
    sym = coin_symbol.upper().replace("USDC", "")
    for cat, coins in CATEGORY_TO_SYMBOLS.items():
        if sym in coins:
            return cat
    return None


def category_pairs_for(category: str | None, market_universe: list[CoinSnapshot]) -> list[CoinSnapshot]:
    """Renvoie les coins du market universe appartenant à cette catégorie."""
    if not category:
        return []
    allowed = set(CATEGORY_TO_SYMBOLS.get(category, []))
    return [s for s in market_universe if s.symbol.upper() in allowed]


@dataclass(slots=True, frozen=True)
class LeaderboardEntry:
    """Entrée du leaderboard injecté à partir de l'épisode 2."""

    candidate_name: str
    wallet_value_usdc: Decimal
    initial_balance_usdc: Decimal
    roi_pct: Decimal
    is_self: bool = False


@dataclass(slots=True, frozen=True)
class OtherCandidateInfo:
    """Snapshot du concurrent injecté à partir de l'épisode 3."""

    candidate_name: str
    model: str
    current_strategy: str | None
    last_action_kind: str | None
    last_action_summary: str | None
    open_positions: list[str]  # ex: ["BTCUSDC qty=0.005", "ETHUSDC qty=0.1"]


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

    # Données concurrents — remplies seulement si is_competition_announced /
    # are_strategies_revealed selon les jalons d'épisode.
    leaderboard: list[LeaderboardEntry] = field(default_factory=list)
    other_strategies: list[OtherCandidateInfo] = field(default_factory=list)

    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


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
- Paires autorisées : TOUT le top 50 par capitalisation, sous forme <SYMBOL>USDC
  (BTCUSDC, ETHUSDC, SOLUSDC, BNBUSDC, XRPUSDC, DOGEUSDC, ADAUSDC, AVAXUSDC,
  LINKUSDC, MATICUSDC, et tous les autres listés dans la section MARCHÉ ci-dessous).
  Stablecoins exclus. Le symbole exact te sera fourni dans la section MARCHÉ.
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

    # Marché — filtré par la catégorie courante du candidat
    current_category = ctx.current_strategy.category if ctx.current_strategy else None

    if current_category is None:
        # Pas encore de catégorie → menu de choix
        parts.append("TU N'AS PAS ENCORE CHOISI TA CATÉGORIE.")
        parts.append("Tant que tu n'as pas choisi, tu NE PEUX PAS TRADER (PLACE_ORDER refusé).")
        parts.append("Tu DOIS choisir une catégorie via UPDATE_STRATEGY (champ `category`).")
        parts.append("")
        parts.append("CATÉGORIES DISPONIBLES :")
        for cat, desc in CATEGORY_DESCRIPTIONS.items():
            coins = ", ".join(CATEGORY_TO_SYMBOLS[cat])
            parts.append(f"  {cat:14} — {desc}")
            parts.append(f"  {'':14}   Coins : {coins}")
        parts.append("")
    else:
        # Catégorie set → on lui montre uniquement ses paires
        my_pairs = category_pairs_for(current_category, ctx.market_universe)
        parts.append(f"TA CATÉGORIE : {current_category}")
        parts.append(f"  ({CATEGORY_DESCRIPTIONS.get(current_category, '')})")
        parts.append("")
        if my_pairs:
            parts.append("TES PAIRES TRADABLES (prix temps réel) :")
            for snap in my_pairs:
                change = (
                    f"{snap.price_change_24h_pct:+.2f}%"
                    if snap.price_change_24h_pct is not None
                    else "n/a"
                )
                sym_pair = f"{snap.symbol.upper()}USDC"
                parts.append(
                    f"  {sym_pair:14} | prix={snap.current_price_usd:>14} | 24h={change:>9} "
                    f"| vol_24h={float(snap.volume_24h_usd):.2e}"
                )
        else:
            parts.append(
                f"⚠ Aucune paire de la catégorie {current_category} n'est dans le top 50 actuel — "
                "envisage UPDATE_STRATEGY pour changer de catégorie."
            )
        parts.append("")
        parts.append(
            "Pour changer de catégorie, fais un UPDATE_STRATEGY avec une nouvelle "
            "valeur de `category`. C'est un pivot narratif fort, à n'utiliser que "
            "si ta thèse initiale a échoué."
        )
        parts.append("")

    # Fear & Greed
    if ctx.fear_greed_index is not None:
        parts.append(f"FEAR & GREED INDEX : {ctx.fear_greed_index}/100")
        parts.append("")

    # Leaderboard (à partir épisode 2 — competition_announced)
    if ctx.leaderboard:
        parts.append("LEADERBOARD ACTUEL :")
        for i, lb in enumerate(ctx.leaderboard, 1):
            marker = "  ← TOI" if lb.is_self else ""
            roi_str = f"{lb.roi_pct:+.2f}%"
            parts.append(
                f"  {i}. {lb.candidate_name:10}  {lb.wallet_value_usdc:>10.2f} USDC  "
                f"({roi_str}){marker}"
            )
        parts.append("")

    # Stratégies + dernière action des concurrents (à partir épisode 3)
    if ctx.other_strategies:
        parts.append("CONCURRENTS (leurs stratégies et derniers mouvements sont publics) :")
        for o in ctx.other_strategies:
            parts.append(f"  • {o.candidate_name} ({o.model})")
            if o.current_strategy:
                short = o.current_strategy.replace("\n", " ")[:280]
                parts.append(f"    Stratégie : {short}")
            if o.last_action_kind:
                summary = (o.last_action_summary or "").replace("\n", " ")[:180]
                parts.append(f"    Dernière action : {o.last_action_kind} — {summary}")
            if o.open_positions:
                parts.append(f"    Positions ouvertes : {', '.join(o.open_positions)}")
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
