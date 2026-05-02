from __future__ import annotations

from plant_monitor.models import SpeciesThresholds, ThresholdRange


SPECIES_THRESHOLDS: dict[str, SpeciesThresholds] = {
    "golden_pothos": SpeciesThresholds(
        moisture=ThresholdRange(min_green=25, min_orange=18),
        temperature=ThresholdRange(min_green=60, min_orange=55, max_green=85, max_orange=90),
        humidity=ThresholdRange(min_green=35, min_orange=25),
    ),
    "ficus_altissima": SpeciesThresholds(
        moisture=ThresholdRange(min_green=30, min_orange=22),
        temperature=ThresholdRange(min_green=60, min_orange=55, max_green=85, max_orange=90),
        humidity=ThresholdRange(min_green=35, min_orange=25),
    ),
    "boston_fern": SpeciesThresholds(
        moisture=ThresholdRange(min_green=40, min_orange=30),
        temperature=ThresholdRange(min_green=60, min_orange=55, max_green=80, max_orange=85),
        humidity=ThresholdRange(min_green=50, min_orange=40),
    ),
    "peperomia_jelly": SpeciesThresholds(
        moisture=ThresholdRange(min_green=25, min_orange=15),
        temperature=ThresholdRange(min_green=60, min_orange=55, max_green=85, max_orange=90),
        humidity=ThresholdRange(min_green=35, min_orange=25),
    ),
}

DEFAULT_THRESHOLDS = SpeciesThresholds(
    moisture=ThresholdRange(min_green=30, min_orange=20),
    temperature=ThresholdRange(min_green=60, min_orange=55, max_green=85, max_orange=90),
    humidity=ThresholdRange(min_green=35, min_orange=25),
)
