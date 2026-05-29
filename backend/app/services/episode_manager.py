"""Episode Manager : calcule le numéro d'épisode courant et la visibilité des canaux.

L'expérience dure 14 jours. Chaque journée est un épisode (épisode 1 = jour
du lancement, épisode 14 = Grande Finale).

Gère également la visibilité des canaux de communication selon le jalon :
- Avant épisode 3 : aucune communication.
- À partir de 3   : canal public + confessionnaux (DMs).
- À partir de 5   : clans (canaux de groupe).
- À partir de 12  : black-out, plus aucun canal ouvert.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.models import Experiment


@dataclass(slots=True, frozen=True)
class ChannelPermissions:
    """Quels canaux sont ouverts à un épisode donné."""

    public: bool
    private: bool
    clan: bool

    def is_allowed(self, channel: str) -> bool:
        return {
            "public": self.public,
            "private": self.private,
            "clan": self.clan,
        }.get(channel, False)


class EpisodeManager:
    """Calculs basés sur l'horloge de l'expérience."""

    def __init__(self, experiment: Experiment) -> None:
        self.experiment = experiment

    # ---------------------------------------------------------------- Episode #

    def current_episode(self, now: datetime | None = None) -> int:
        """Numéro d'épisode courant, basé sur le temps écoulé depuis started_at.

        - Avant started_at : 0.
        - 0 à 24h après : 1.
        - 24 à 48h : 2.
        - ...
        - Plafonné à duration_days.
        """
        now = now or datetime.now(timezone.utc)
        if self.experiment.started_at.tzinfo is None:
            # Defensive : si stocké naive, on suppose UTC
            started = self.experiment.started_at.replace(tzinfo=timezone.utc)
        else:
            started = self.experiment.started_at
        if now < started:
            return 0
        delta_days = (now - started).total_seconds() / 86400.0
        episode = int(delta_days) + 1
        return min(episode, self.experiment.duration_days)

    def is_finished(self, now: datetime | None = None) -> bool:
        return self.current_episode(now) >= self.experiment.duration_days

    def episode_start_at(self, episode_number: int) -> datetime:
        """Datetime de début d'un épisode donné."""
        return self.experiment.started_at + timedelta(days=episode_number - 1)

    # -------------------------------------------------------------- Channels

    @staticmethod
    def channel_permissions(episode_number: int) -> ChannelPermissions:
        """Quels canaux sont ouverts à cet épisode."""
        if episode_number < 3:
            return ChannelPermissions(public=False, private=False, clan=False)
        if episode_number < 5:
            return ChannelPermissions(public=True, private=True, clan=False)
        if episode_number < 12:
            return ChannelPermissions(public=True, private=True, clan=True)
        return ChannelPermissions(public=False, private=False, clan=False)

    # ------------------------------------------------------ Reveal flags

    @staticmethod
    def competition_announced(episode_number: int) -> bool:
        """Les candidats savent-ils qu'ils sont en compétition ?"""
        return episode_number >= 2

    @staticmethod
    def strategies_revealed(episode_number: int) -> bool:
        """Les stratégies et historiques des autres candidats sont-ils visibles ?"""
        return episode_number >= 3

    @staticmethod
    def is_grand_finale(episode_number: int, duration_days: int = 14) -> bool:
        return episode_number == duration_days
