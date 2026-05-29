"""Service Team Council : orchestration de la délibération à 5 modèles.

Quand le candidat équipe (`kind="team"`) prend une décision, on n'appelle
pas un seul modèle. À la place, on lance un débat entre les 5 modèles
(Claude, ChatGPT, Mistral, Gemini, Grok). Chacun produit, à son tour,
un message au reste du conseil + une action proposée. La majorité simple
sur le type d'action décide.

Architecture :
- TeamMember : (nom affiché, modèle, clé provider dans le gateway)
- TeamSpeech (Pydantic) : ce que produit un modèle à son tour
- run_council_round : appelle les 5 modèles en séquence pour un tour
- count_votes / pick_winner : agrégation des votes
- run_council_deliberation : pilote les tours jusqu'à majorité ou max
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from app.services.llm_gateway.actions import (
    Action,
    HoldAction,
    action_schema_for_prompt,
)
from app.services.llm_gateway.base import LLMRequest
from app.services.llm_gateway.gateway import extract_json

logger = logging.getLogger(__name__)


# =============================================================================
# Constantes
# =============================================================================


DEFAULT_MAX_TURNS = 2
DEFAULT_MAJORITY_THRESHOLD = 3  # 3 voix sur 5


# =============================================================================
# Types
# =============================================================================


@dataclass(slots=True, frozen=True)
class TeamMember:
    """Un membre du conseil : qui parle, avec quel modèle, via quel provider."""

    name: str
    model: str
    provider: str


class TeamSpeech(BaseModel):
    """Le format JSON qu'un membre du conseil doit produire à son tour."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message_to_team: str = Field(
        ...,
        min_length=1,
        max_length=1500,
        description=(
            "Message adressé au reste du conseil. 1-3 phrases. "
            "Tu peux argumenter, contredire, proposer une alternative."
        ),
    )
    proposed_action: Action = Field(
        ...,
        description=(
            "Action concrète que tu proposes pour le conseil. "
            "Si la majorité (3+) vote la même action_kind, elle sera exécutée."
        ),
    )


_TEAM_SPEECH_ADAPTER: TypeAdapter[TeamSpeech] = TypeAdapter(TeamSpeech)


@dataclass(slots=True)
class TurnRecord:
    """Trace en mémoire d'une intervention pendant la délibération."""

    turn_number: int
    speaker_name: str
    speaker_model: str
    speaker_provider: str
    message_to_team: str
    proposed_action: Action | None
    proposed_action_kind: str | None
    raw_response: str
    parse_error: str | None
    tokens_input: int
    tokens_output: int
    latency_ms: int
    created_at: datetime


# =============================================================================
# Parsing
# =============================================================================


def parse_team_speech(raw_text: str) -> tuple[TeamSpeech | None, str | None]:
    """Tente d'extraire un TeamSpeech valide d'une réponse LLM brute."""
    try:
        json_text = extract_json(raw_text)
    except json.JSONDecodeError as e:
        return None, f"No valid JSON in response: {e}"
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as e:  # pragma: no cover
        return None, f"JSON decode error: {e}"
    try:
        speech = _TEAM_SPEECH_ADAPTER.validate_python(payload)
        return speech, None
    except ValidationError as e:
        return None, f"Schema validation failed: {e.errors(include_url=False)}"


# =============================================================================
# Prompts
# =============================================================================


_TEAM_SYSTEM_TEMPLATE = """Tu es {speaker_name}, l'un des 5 membres du Conseil Team
de l'expérience « Les Anges de l'IA ». Le conseil partage un compte commun de
trading crypto avec un capital virtuel de 10 USDC, indépendant des candidats
individuels qui vous correspondent.

LES 5 MEMBRES DU CONSEIL :
{members_list}

DÉROULÉ D'UN TOUR.
- Chaque membre parle à son tour, dans l'ordre.
- Tu vois tous les messages déjà postés par les membres précédents pendant
  ce cycle (tours précédents et tour en cours).
- Tu adresses un message court (1-3 phrases) au reste du conseil. Tu peux
  argumenter ton point de vue, contredire un autre membre, proposer un compromis.
- Tu proposes une action concrète parmi : PLACE_ORDER, CLOSE_POSITION,
  UPDATE_STRATEGY, QUERY_ORACLE, SEND_MESSAGE, HOLD.
- Si une action obtient la majorité (3+ voix sur 5), elle sera exécutée.
- Sinon, un nouveau tour de délibération commence.

LIBERTÉS DE TON.
Tu peux dire ce que tu penses sans filtre — défendre une position risquée,
appeler les autres à la prudence, manifester ton désaccord. Mais tu dois
finir par proposer une action.

FORMAT DE RÉPONSE (impératif).
Un unique objet JSON conforme au schéma :
{{
  "message_to_team": "Ton intervention au conseil (1-3 phrases, max 1500 chars).",
  "proposed_action": {{ /* objet action standard, voir schémas ci-dessous */ }}
}}

ACTIONS POSSIBLES — chaque type est décrit ci-dessous (à mettre dans `proposed_action`) :
{schema}

Pas de texte hors du JSON. Pas de markdown.
"""


def build_team_system_prompt(
    speaker_name: str, all_members: list[TeamMember]
) -> str:
    members_list = "\n".join(
        f"- {m.name} ({m.model})" for m in all_members
    )
    return _TEAM_SYSTEM_TEMPLATE.format(
        speaker_name=speaker_name,
        members_list=members_list,
        schema=action_schema_for_prompt(),
    )


def build_blind_pitch_user_prompt(base_user_prompt: str) -> str:
    """Prompt pour le pitch initial à l'aveugle (tour 0).

    Le membre écrit sa proposition SANS voir celles des autres — il évite ainsi
    l'effet de cascade informationnelle où le 2ᵉ s'aligne sur le 1ᵉʳ.
    Sa réponse forme avec les 4 autres pitchs aveugles l'ouverture du débat.
    """
    return (
        base_user_prompt
        + "\n\n=== PITCH INITIAL À L'AVEUGLE ===\n"
        + "Tu écris ta proposition de stratégie SANS voir celles des autres "
        + "membres du conseil. Tes 4 collègues écrivent en parallèle. Cette "
        + "phase capture les vraies opinions de départ avant que la dynamique "
        + "de groupe ne s'installe.\n\n"
        + "Sois CONCRET et OPINIONÉ : propose l'action que TU ferais, sans "
        + "chercher le consensus. Tu auras tout le débat pour ajuster ensuite.\n\n"
        + "=== À TOI ===\n"
        + "Réponds avec le JSON {message_to_team, proposed_action}."
    )


def build_team_user_prompt(
    base_user_prompt: str,
    history: list[TurnRecord],
    current_turn: int,
) -> str:
    """Enrichit le user_prompt standard avec l'historique de la délibération.

    Affiche d'abord le bloc PITCH INITIAL (tour 0) puis les tours de débat.
    """
    pitches = [h for h in history if h.turn_number == 0]
    debates = [h for h in history if h.turn_number >= 1]

    sections = []
    if pitches:
        pitch_lines = []
        for h in pitches:
            kind = h.proposed_action_kind or "—"
            pitch_lines.append(
                f"  • {h.speaker_name} → {kind}\n    « {h.message_to_team} »"
            )
        sections.append("PITCHS INITIAUX (chacun les a écrits sans voir les autres) :\n" + "\n".join(pitch_lines))

    if debates:
        debate_lines = []
        for h in debates:
            kind = h.proposed_action_kind or "—"
            debate_lines.append(
                f"[Tour {h.turn_number}] {h.speaker_name} → propose {kind}\n  « {h.message_to_team} »"
            )
        sections.append("DÉBAT EN COURS :\n" + "\n\n".join(debate_lines))

    history_text = "\n\n".join(sections) if sections else (
        "Tu es le premier à parler. Aucun membre n'a encore donné son avis. Pose le débat."
    )

    return (
        base_user_prompt
        + "\n\n=== DÉLIBÉRATION ===\n"
        + f"Tour {current_turn} sur {DEFAULT_MAX_TURNS}.\n\n"
        + history_text
        + "\n\n=== À TOI DE PARLER ===\n"
        + "Tu vois les pitchs initiaux des autres et le débat. "
        + "Tu peux maintenir ta position, te rallier, ou proposer un compromis. "
        + "Réponds avec le JSON {message_to_team, proposed_action}."
    )


# =============================================================================
# Vote counting
# =============================================================================


@dataclass(slots=True)
class VoteOutcome:
    has_majority: bool
    winning_kind: str | None
    winning_action: Action | None
    counts: dict[str, int] = field(default_factory=dict)


def tally_votes(
    records: list[TurnRecord],
    turn_number: int | None = None,
    threshold: int = DEFAULT_MAJORITY_THRESHOLD,
) -> VoteOutcome:
    """Compte les votes des membres ayant parlé jusqu'ici.

    Si un membre a parlé à plusieurs tours, son DERNIER vote compte.
    """
    if turn_number is not None:
        relevant = [r for r in records if r.turn_number <= turn_number]
    else:
        relevant = records

    # Dernier vote par membre
    last_vote: dict[str, TurnRecord] = {}
    for r in relevant:
        if r.proposed_action_kind is None:
            continue
        last_vote[r.speaker_name] = r

    if not last_vote:
        return VoteOutcome(has_majority=False, winning_kind=None, winning_action=None, counts={})

    counts = Counter(r.proposed_action_kind for r in last_vote.values())
    top_kind, top_count = counts.most_common(1)[0]
    has_maj = top_count >= threshold

    # Action gagnante = la dernière proposition correspondante (la plus récente)
    winning_action: Action | None = None
    if has_maj:
        for r in reversed(relevant):
            if r.proposed_action_kind == top_kind and r.proposed_action is not None:
                winning_action = r.proposed_action
                break

    return VoteOutcome(
        has_majority=has_maj,
        winning_kind=top_kind if has_maj else None,
        winning_action=winning_action if has_maj else None,
        counts=dict(counts),
    )


# =============================================================================
# Conduite de la délibération
# =============================================================================


async def _call_member(
    member: TeamMember,
    system_prompt: str,
    user_prompt: str,
    gateway,  # noqa: ANN001
    turn_number: int,
) -> TurnRecord:
    """Appelle un membre, parse sa réponse, renvoie un TurnRecord.
    Persiste l'erreur si exception (pas de silencieux)."""
    request = LLMRequest(
        candidate_name=f"Team:{member.name}",
        model=member.model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )
    provider = gateway.providers.get(member.provider)
    now = datetime.now(timezone.utc)

    if provider is None:
        return TurnRecord(
            turn_number=turn_number, speaker_name=member.name,
            speaker_model=member.model, speaker_provider=member.provider,
            message_to_team="(provider non configuré)", proposed_action=None,
            proposed_action_kind=None, raw_response="",
            parse_error=f"no provider {member.provider}",
            tokens_input=0, tokens_output=0, latency_ms=0, created_at=now,
        )

    try:
        response = await provider.complete(request)
    except Exception as e:
        logger.exception("Team member %s failed", member.name)
        return TurnRecord(
            turn_number=turn_number, speaker_name=member.name,
            speaker_model=member.model, speaker_provider=member.provider,
            message_to_team=f"⚠️ {type(e).__name__} — {str(e)[:200]}",
            proposed_action=None, proposed_action_kind=None,
            raw_response="", parse_error=f"{type(e).__name__}: {e}",
            tokens_input=0, tokens_output=0, latency_ms=0, created_at=now,
        )

    speech, parse_error = parse_team_speech(response.raw_text)
    return TurnRecord(
        turn_number=turn_number, speaker_name=member.name,
        speaker_model=member.model, speaker_provider=member.provider,
        message_to_team=speech.message_to_team if speech else "(parse failed)",
        proposed_action=speech.proposed_action if speech else None,
        proposed_action_kind=speech.proposed_action.action.value if speech else None,
        raw_response=response.raw_text, parse_error=parse_error,
        tokens_input=response.tokens_input, tokens_output=response.tokens_output,
        latency_ms=response.latency_ms, created_at=now,
    )


async def run_council_deliberation(
    members: list[TeamMember],
    gateway,  # noqa: ANN001 (LLMGateway, avoid circular import)
    base_user_prompt: str,
    max_turns: int = DEFAULT_MAX_TURNS,
    threshold: int = DEFAULT_MAJORITY_THRESHOLD,
) -> tuple[list[TurnRecord], Action, VoteOutcome]:
    """Pilote la délibération du conseil.

    Phase 0 : PITCH INITIAL — les 5 membres écrivent en PARALLÈLE, sans voir
    les autres. Évite l'effet de cascade où le 2ᵉ s'aligne sur le 1ᵉʳ.
    Phase 1+ : DÉBAT OUVERT — séquentiel, avec ROULEMENT de l'ordre à chaque
    tour (shift circulaire) pour que personne ne monopolise l'ouverture.
    Tally après chaque tour de débat (les pitchs ne comptent PAS comme votes).

    Renvoie (historique complet, action retenue, résultat du vote).
    """
    history: list[TurnRecord] = []

    # === Phase 0 : pitchs aveugles en parallèle ===
    pitch_prompts = [
        (build_team_system_prompt(m.name, members), build_blind_pitch_user_prompt(base_user_prompt), m)
        for m in members
    ]
    pitch_records = await asyncio.gather(
        *(_call_member(m, sys_p, usr_p, gateway, turn_number=0) for sys_p, usr_p, m in pitch_prompts)
    )
    history.extend(pitch_records)

    # Pas de tally après phase 0 — les pitchs sont juste exploratoires.

    # === Phase 1+ : débat séquentiel avec roulement ===
    last_outcome: VoteOutcome | None = None
    for turn in range(1, max_turns + 1):
        # Roulement circulaire : tour 1 → ordre normal, tour 2 → shift d'1, etc.
        shift = (turn - 1) % len(members)
        turn_order = members[shift:] + members[:shift]
        for member in turn_order:
            system_prompt = build_team_system_prompt(member.name, members)
            user_prompt = build_team_user_prompt(base_user_prompt, history, turn)
            record = await _call_member(member, system_prompt, user_prompt, gateway, turn_number=turn)
            history.append(record)

        # Tally après que tous les membres aient parlé pour ce tour
        outcome = tally_votes(history, turn_number=turn, threshold=threshold)
        last_outcome = outcome
        if outcome.has_majority:
            return history, outcome.winning_action, outcome  # type: ignore[return-value]

    # Pas de majorité après max_turns → fallback HOLD
    fallback = HoldAction(
        reasoning_summary=(
            f"Aucune majorité après {max_turns} tours de délibération. "
            f"Votes : {last_outcome.counts if last_outcome else {}}"
        )
    )
    return (
        history,
        fallback,
        last_outcome or VoteOutcome(False, None, None, {}),
    )


# =============================================================================
# Helpers exposés pour les tests
# =============================================================================


def speech_to_dict(speech: TeamSpeech) -> dict[str, Any]:
    return speech.model_dump(mode="json")
