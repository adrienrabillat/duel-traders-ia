"""Tests de l'EpisodeManager."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models import Experiment
from app.services.episode_manager import EpisodeManager


@pytest.fixture
def experiment() -> Experiment:
    return Experiment(
        id=1,
        name="Les Anges de l'IA",
        started_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        status="paper",
        duration_days=14,
    )


class TestEpisodeNumber:
    def test_before_start(self, experiment: Experiment) -> None:
        manager = EpisodeManager(experiment)
        before = experiment.started_at - timedelta(hours=1)
        assert manager.current_episode(before) == 0

    def test_first_hour(self, experiment: Experiment) -> None:
        manager = EpisodeManager(experiment)
        assert manager.current_episode(experiment.started_at) == 1

    def test_day_1(self, experiment: Experiment) -> None:
        manager = EpisodeManager(experiment)
        t = experiment.started_at + timedelta(hours=23)
        assert manager.current_episode(t) == 1

    def test_day_2(self, experiment: Experiment) -> None:
        manager = EpisodeManager(experiment)
        t = experiment.started_at + timedelta(days=1, hours=1)
        assert manager.current_episode(t) == 2

    def test_capped_at_duration(self, experiment: Experiment) -> None:
        manager = EpisodeManager(experiment)
        t = experiment.started_at + timedelta(days=20)
        assert manager.current_episode(t) == 14

    def test_is_finished(self, experiment: Experiment) -> None:
        manager = EpisodeManager(experiment)
        t = experiment.started_at + timedelta(days=14, hours=1)
        assert manager.is_finished(t)


class TestChannelPermissions:
    def test_no_channels_episode_1_2(self) -> None:
        for ep in (1, 2):
            p = EpisodeManager.channel_permissions(ep)
            assert not p.public and not p.private and not p.clan

    def test_episode_3_opens_public_and_private(self) -> None:
        p = EpisodeManager.channel_permissions(3)
        assert p.public and p.private and not p.clan

    def test_episode_5_opens_clans(self) -> None:
        p = EpisodeManager.channel_permissions(5)
        assert p.public and p.private and p.clan

    def test_episode_12_blackout(self) -> None:
        p = EpisodeManager.channel_permissions(12)
        assert not p.public and not p.private and not p.clan

    def test_episode_14_blackout(self) -> None:
        p = EpisodeManager.channel_permissions(14)
        assert not p.public and not p.private and not p.clan

    def test_is_allowed(self) -> None:
        p = EpisodeManager.channel_permissions(5)
        assert p.is_allowed("public")
        assert p.is_allowed("private")
        assert p.is_allowed("clan")
        assert not p.is_allowed("unknown")


class TestReveal:
    def test_competition_announced_from_episode_2(self) -> None:
        assert not EpisodeManager.competition_announced(1)
        assert EpisodeManager.competition_announced(2)

    def test_strategies_revealed_from_episode_3(self) -> None:
        assert not EpisodeManager.strategies_revealed(2)
        assert EpisodeManager.strategies_revealed(3)

    def test_grand_finale(self) -> None:
        assert not EpisodeManager.is_grand_finale(13)
        assert EpisodeManager.is_grand_finale(14)
