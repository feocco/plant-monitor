from __future__ import annotations

from plant_monitor.models import SpeciesThresholds, ThresholdRange


SPECIES_THRESHOLDS: dict[str, SpeciesThresholds] = {
    "golden_pothos": SpeciesThresholds(
        moisture=ThresholdRange(min_green=25, min_orange=18, max_green=70, max_orange=82),
        temperature=ThresholdRange(min_green=60, min_orange=55, max_green=85, max_orange=90),
        humidity=ThresholdRange(min_green=35, min_orange=25),
    ),
    "ficus_altissima": SpeciesThresholds(
        moisture=ThresholdRange(min_green=30, min_orange=22, max_green=72, max_orange=84),
        temperature=ThresholdRange(min_green=60, min_orange=55, max_green=85, max_orange=90),
        humidity=ThresholdRange(min_green=35, min_orange=25),
    ),
    "boston_fern": SpeciesThresholds(
        moisture=ThresholdRange(min_green=40, min_orange=30, max_green=82, max_orange=92),
        temperature=ThresholdRange(min_green=60, min_orange=55, max_green=80, max_orange=85),
        humidity=ThresholdRange(min_green=50, min_orange=40),
    ),
    "peperomia_jelly": SpeciesThresholds(
        moisture=ThresholdRange(min_green=25, min_orange=15, max_green=65, max_orange=78),
        temperature=ThresholdRange(min_green=60, min_orange=55, max_green=85, max_orange=90),
        humidity=ThresholdRange(min_green=35, min_orange=25),
    ),
    "wandering_dude": SpeciesThresholds(
        moisture=ThresholdRange(min_green=25, min_orange=15, max_green=75, max_orange=85),
        temperature=ThresholdRange(min_green=65, min_orange=55, max_green=85, max_orange=90),
        humidity=ThresholdRange(min_green=35, min_orange=25),
    ),
    "outdoor_mixed_vegetable_container": SpeciesThresholds(
        moisture=ThresholdRange(min_green=35, min_orange=25, max_green=80, max_orange=90),
        temperature=ThresholdRange(min_green=45, min_orange=40, max_green=95, max_orange=100),
        humidity=ThresholdRange(min_green=30, min_orange=20),
    ),
    "outdoor_mixed_annual_hanging_basket": SpeciesThresholds(
        moisture=ThresholdRange(min_green=35, min_orange=25, max_green=88, max_orange=95),
        temperature=ThresholdRange(min_green=45, min_orange=40, max_green=95, max_orange=100),
        humidity=ThresholdRange(min_green=30, min_orange=20),
    ),
    "fuchsia_hanging_basket": SpeciesThresholds(
        moisture=ThresholdRange(min_green=40, min_orange=30, max_green=88, max_orange=95),
        temperature=ThresholdRange(min_green=45, min_orange=40, max_green=90, max_orange=95),
        humidity=ThresholdRange(min_green=35, min_orange=25),
    ),
}

DEFAULT_THRESHOLDS = SpeciesThresholds(
    moisture=ThresholdRange(min_green=30, min_orange=20, max_green=75, max_orange=85),
    temperature=ThresholdRange(min_green=60, min_orange=55, max_green=85, max_orange=90),
    humidity=ThresholdRange(min_green=35, min_orange=25),
)
