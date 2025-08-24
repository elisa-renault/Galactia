# /home/Galactia/core/feature_flags.py
"""Feature flag helper for Galactia.

This module provides simple in-memory caching of enabled features. The
``refresh_feature_flags`` function loads all enabled ``GuildFeatureFlag`` rows
from the database and stores them in module-level dictionaries. The
``is_feature_enabled`` function can then be used by other parts of the
application to quickly check if a feature is enabled globally or for a
specific guild (identified by its Discord guild ID).
"""
from __future__ import annotations

from typing import Dict, Set, Optional
import logging
from sqlalchemy import select

from core.db import SessionLocal
from core.models import Feature, Guild, GuildFeatureFlag

# Cache structures populated by ``refresh_feature_flags``.
_global_features: Set[str] = set()
_guild_features: Dict[int, Set[str]] = {}


def refresh_feature_flags() -> None:
    """Reload feature flags from the database into the in-memory cache."""
    global _global_features, _guild_features

    with SessionLocal() as db:
        rows = db.execute(
            select(Guild.discord_id, Feature.key)
            .join(GuildFeatureFlag, GuildFeatureFlag.guild_id == Guild.id)
            .join(Feature, Feature.id == GuildFeatureFlag.feature_id)
            .where(GuildFeatureFlag.enabled == True)  # noqa: E712
        ).all()

    global_set: Set[str] = set()
    guild_map: Dict[int, Set[str]] = {}

    for guild_discord_id, feature_key in rows:
        if guild_discord_id in (None, 0):
            global_set.add(feature_key)
        else:
            guild_features = guild_map.setdefault(int(guild_discord_id), set())
            guild_features.add(feature_key)

    _global_features = global_set
    _guild_features = guild_map
    logging.info(
        "Feature flags refreshed: %s global features, %s guilds",
        len(_global_features),
        len(_guild_features),
    )


def is_feature_enabled(guild_id: Optional[int], feature_key: str) -> bool:
    """Return ``True`` if the feature is enabled globally or for ``guild_id``.

    Parameters
    ----------
    guild_id:
        Discord guild ID for which to check the feature. ``None`` checks only
        for globally-enabled features.
    feature_key:
        The textual key of the feature, e.g. ``"twitch"`` or ``"ai"``.
    """
    if guild_id is not None:
        features = _guild_features.get(int(guild_id))
        if features and feature_key in features:
            return True
    return feature_key in _global_features
