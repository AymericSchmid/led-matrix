"""Shared scene metadata; renderer dispatch remains in HeadlessScene."""
from dataclasses import dataclass

@dataclass(frozen=True)
class SceneDefinition:
    id: str
    label: str
    voice_label_fr: str
    icon: str
    options: frozenset[str]

_OPTIONS = {
    "clock": frozenset(("theme", "no_numbers", "no_ticks", "hand_length_percent", "hand_width_percent")),
    "spotify": frozenset(("spotify_style", "no_progress", "no_drift_cover", "drift_blackness")),
    "weather": frozenset(("weather_city", "weather_demo", "weather_hour")),
    "eyes": frozenset(("eyes_movement_frequency", "eyes_movement_speed", "eyes_blink_rate")),
    "kintsugi": frozenset(("kintsugi_theme", "kintsugi_speed", "kintsugi_density")),
    "shadow": frozenset(("shadow_speed", "shadow_frequency", "shadow_intensity")),
}

SCENE_CATALOGUE = (
    SceneDefinition('clock', 'Clock', 'Horloge', '◷', _OPTIONS.get('clock', frozenset())),
    SceneDefinition('circle', 'Circle', 'Cercle', '◯', _OPTIONS.get('circle', frozenset())),
    SceneDefinition('spotify', 'Spotify', 'Spotify', '♫', _OPTIONS.get('spotify', frozenset())),
    SceneDefinition('weather', 'Weather glass', 'Météo', '☀', _OPTIONS.get('weather', frozenset())),
    SceneDefinition('moon', 'Moon', 'Lune', '☾', _OPTIONS.get('moon', frozenset())),
    SceneDefinition('eyes', 'Eyes', 'Yeux', '◉', _OPTIONS.get('eyes', frozenset())),
    SceneDefinition('walker', 'Night walker', 'Marcheur', '♟', _OPTIONS.get('walker', frozenset())),
    SceneDefinition('kintsugi', 'Kintsugi', 'Kintsugi', '◇', _OPTIONS.get('kintsugi', frozenset())),
    SceneDefinition('shadow', 'Impossible shadow', 'Ombre', '◌', _OPTIONS.get('shadow', frozenset())),
)
SCENE_IDS = tuple(scene.id for scene in SCENE_CATALOGUE)
SCENE_OPTIONS = {scene.id: scene.options for scene in SCENE_CATALOGUE}

def scene_metadata() -> list[dict]:
    return [dict(id=s.id, label=s.label, voice_label_fr=s.voice_label_fr,
                 icon=s.icon, options=sorted(s.options)) for s in SCENE_CATALOGUE]
