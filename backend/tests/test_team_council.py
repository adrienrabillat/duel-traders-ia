"""Tests de la délibération du Team Council."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.models import Experiment, TeamDeliberation
from app.services.episode_manager import EpisodeManager
from app.services.ledger import Ledger
from app.services.llm_gateway import LLMGateway, MockProvider
from app.services.orchestrator import Orchestrator
from app.services.team_council import (
    DEFAULT_MAJORITY_THRESHOLD,
    TeamMember,
    TurnRecord,
    parse_team_speech,
    tally_votes,
)


def _make_experiment(db, started_at: datetime, duration: int = 14) -> Experiment:
    exp = Experiment(name="Test", started_at=started_at, status="paper", duration_days=duration)
    db.add(exp)
    db.flush()
    return exp


def _speech_json(action: dict, message: str = "OK on y va") -> str:
    return json.dumps({"message_to_team": message, "proposed_action": action})


HOLD_ACTION = {"action": "HOLD", "reasoning_summary": "j'attends"}
BUY_ACTION = {
    "action": "PLACE_ORDER",
    "side": "BUY",
    "symbol": "BTCUSDC",
    "quote_amount_usdc": "8",
    "stop_loss_pct": "3",
    "take_profit_pct": "5",
    "reasoning_summary": "setup haussier",
}
UPDATE_STRATEGY_ACTION = {
    "action": "UPDATE_STRATEGY",
    "new_strategy": "On switch sur les memes en marché de range, prudent sur les majors.",
    "reasoning_summary": "marché plat",
}


# =============================================================================
# parse_team_speech
# =============================================================================


class TestParseTeamSpeech:
    def test_valid_speech(self) -> None:
        raw = _speech_json(HOLD_ACTION, "On reste prudent.")
        speech, err = parse_team_speech(raw)
        assert err is None
        assert speech is not None
        assert speech.message_to_team == "On reste prudent."
        assert speech.proposed_action.action.value == "HOLD"

    def test_valid_speech_in_markdown_fence(self) -> None:
        raw = "```json\n" + _speech_json(BUY_ACTION) + "\n```"
        speech, err = parse_team_speech(raw)
        assert err is None and speech is not None

    def test_missing_message_field(self) -> None:
        speech, err = parse_team_speech(json.dumps({"proposed_action": HOLD_ACTION}))
        assert speech is None and err

    def test_invalid_action_inside(self) -> None:
        speech, err = parse_team_speech(
            json.dumps({"message_to_team": "x", "proposed_action": {"action": "DANCE"}})
        )
        assert speech is None and err


# =============================================================================
# tally_votes
# =============================================================================


def _record(turn: int, name: str, kind: str | None) -> TurnRecord:
    return TurnRecord(
        turn_number=turn,
        speaker_name=name,
        speaker_model="m",
        speaker_provider="p",
        message_to_team="...",
        proposed_action=None,
        proposed_action_kind=kind,
        raw_response="",
        parse_error=None,
        tokens_input=0,
        tokens_output=0,
        latency_ms=0,
        created_at=datetime.now(timezone.utc),
    )


class TestTallyVotes:
    def test_majority_reached(self) -> None:
        records = [
            _record(1, "Claude", "PLACE_ORDER"),
            _record(1, "ChatGPT", "PLACE_ORDER"),
            _record(1, "Mistral", "PLACE_ORDER"),
            _record(1, "Gemini", "HOLD"),
            _record(1, "Grok", "HOLD"),
        ]
        outcome = tally_votes(records)
        assert outcome.has_majority
        assert outcome.winning_kind == "PLACE_ORDER"
        assert outcome.counts == {"PLACE_ORDER": 3, "HOLD": 2}

    def test_no_majority(self) -> None:
        records = [
            _record(1, "Claude", "PLACE_ORDER"),
            _record(1, "ChatGPT", "PLACE_ORDER"),
            _record(1, "Mistral", "HOLD"),
            _record(1, "Gemini", "HOLD"),
            _record(1, "Grok", "UPDATE_STRATEGY"),
        ]
        outcome = tally_votes(records)
        assert not outcome.has_majority
        assert outcome.winning_kind is None

    def test_last_vote_per_member(self) -> None:
        # Claude change d'avis au tour 2 et rejoint la majorité
        records = [
            _record(1, "Claude", "HOLD"),
            _record(1, "ChatGPT", "PLACE_ORDER"),
            _record(1, "Mistral", "PLACE_ORDER"),
            _record(1, "Gemini", "HOLD"),
            _record(1, "Grok", "HOLD"),
            _record(2, "Claude", "PLACE_ORDER"),
        ]
        outcome = tally_votes(records)
        # Maintenant : PLACE_ORDER 3, HOLD 2
        assert outcome.has_majority
        assert outcome.winning_kind == "PLACE_ORDER"


# =============================================================================
# Orchestrator team cycle (integration)
# =============================================================================


def _members_with_speeches(speech_per_member: dict[str, str]) -> tuple[list[TeamMember], dict[str, MockProvider]]:
    """Construit 5 TeamMembers + un MockProvider par membre, chacun
    renvoyant exactement la speech qu'on lui demande."""
    members: list[TeamMember] = []
    providers: dict[str, MockProvider] = {}
    for name, speech in speech_per_member.items():
        provider_key = f"provider_{name}"
        members.append(TeamMember(name=name, model=f"model-{name}", provider=provider_key))
        providers[provider_key] = MockProvider(speech)
    return members, providers


class TestTeamCycleIntegration:
    async def test_unanimous_buy(self, db, ledger: Ledger) -> None:
        team = ledger.create_candidate(
            "Team", "consensus-5", Decimal("10"), kind="team"
        )
        experiment = _make_experiment(db, datetime.now(timezone.utc))

        speeches = {
            name: _speech_json(BUY_ACTION, f"{name}: go !")
            for name in ("Claude", "ChatGPT", "Mistral", "Gemini", "Grok")
        }
        members, providers = _members_with_speeches(speeches)
        gateway = LLMGateway(providers)

        orchestrator = Orchestrator(
            db,
            experiment,
            ledger,
            gateway,
            EpisodeManager(experiment),
            team_members=members,
        )
        result = await orchestrator.run_candidate_cycle(
            team, provider_name="anything-since-team-overrides"
        )
        # L'action est PLACE_ORDER, donc un OrderRecord a été créé en pending_validation
        assert result.action_kind == "PLACE_ORDER"
        assert result.order_id is not None

        # 5 lignes de délibération
        delibs = db.query(TeamDeliberation).all()
        assert len(delibs) == 5
        assert all(d.turn_number == 1 for d in delibs)
        assert {d.speaker_name for d in delibs} == set(speeches.keys())

    async def test_no_majority_falls_back_to_hold(self, db, ledger: Ledger) -> None:
        team = ledger.create_candidate(
            "Team", "consensus-5", Decimal("10"), kind="team"
        )
        experiment = _make_experiment(db, datetime.now(timezone.utc))

        # 5 actions toutes différentes → aucune majorité
        speeches = {
            "Claude": _speech_json(BUY_ACTION),
            "ChatGPT": _speech_json(HOLD_ACTION),
            "Mistral": _speech_json(UPDATE_STRATEGY_ACTION),
            "Gemini": _speech_json(
                {"action": "QUERY_ORACLE", "question": "What about ETH this week?", "reasoning_summary": "infos"}
            ),
            "Grok": _speech_json(
                {"action": "CLOSE_POSITION", "symbol": "BTCUSDC", "reasoning_summary": "rien"}
            ),
        }
        members, providers = _members_with_speeches(speeches)
        gateway = LLMGateway(providers)

        orchestrator = Orchestrator(
            db, experiment, ledger, gateway, EpisodeManager(experiment), team_members=members
        )
        # Avec MockProvider qui retourne TOUJOURS la même speech, même au tour 2
        # → toujours pas de majorité → HOLD fallback
        result = await orchestrator.run_candidate_cycle(team, provider_name="x")
        assert result.action_kind == "HOLD"

        # 10 lignes de délibération (2 tours x 5 membres)
        delibs = db.query(TeamDeliberation).all()
        assert len(delibs) == 10
