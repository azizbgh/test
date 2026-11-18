"""Unveränderliche Messwert- und Statusdaten sowie Sensorbewertung.

SensorReading beschreibt genau einen Eingang: Rohstrom, Empfangszeit und
gegebenenfalls einen Fehlergrund. evaluate_sensor_currents bewertet GHF und
GLF einzeln. DaqState beschreibt den Softwarezustand der vorhandenen Tasks.

Eine dataclass ist ein Datenbehälter mit automatisch erzeugter Initialisierung.
frozen=True verhindert nachträgliche Feldzuweisungen. Worker und Oberfläche
können solche Werte über Queues austauschen, ohne NI-Task-Objekte weiterzugeben.
Diese Datei greift weder auf Hardware noch auf die Oberfläche zu.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .konfiguration import (
    MAX_MEASUREMENT_AGE_S,
    SENSOR_CHANNELS,
    SENSOR_VALID_MAX_A,
    SENSOR_VALID_MIN_A,
)


@dataclass(frozen=True)
class SensorReading:
    """Rohstrom, Empfangszeit und Fehlergrund genau eines Eingangskanals.

    Auch ein unplausibler Strom ist ein empfangener Rohwert. Er wird angezeigt,
    gilt aber nicht als gültige Durchflussmessung oder als Ausgabefreigabe.
    """

    current_a: float | None = None
    received_at: float = 0.0
    problem: str | None = "Warte auf Messdaten"

    @property
    def valid(self) -> bool:
        return (
            self.problem is None
            and self.current_a is not None
            and math.isfinite(self.current_a)
            and SENSOR_VALID_MIN_A <= self.current_a <= SENSOR_VALID_MAX_A
        )

    def recent(self, now: float) -> bool:
        # Frische wird auch für unplausible Rohwerte bestimmt. 0 mA ist nicht
        # dasselbe wie ein fehlender Wert; daher niemals per Wahrheitswert prüfen.
        return self.received_at > 0 and now - self.received_at <= MAX_MEASUREMENT_AGE_S


def evaluate_sensor_currents(currents, received_at: float) -> dict[str, SensorReading]:
    """Jeden Sensor unabhängig prüfen, ohne Hardware oder Widgets anzusprechen."""
    if currents is None:
        currents = (None, None)
    if len(currents) != len(SENSOR_CHANNELS):
        raise ValueError("Messdaten enthalten nicht die erwarteten zwei Kanäle.")
    readings = {}
    for name, current in zip(SENSOR_CHANNELS, currents):
        if current is None:
            problem = "Keine neuen Samples · Messwert fehlt"
        elif not math.isfinite(current):
            problem = "Ungültiger Zahlenwert im Sensorsignal"
        elif not SENSOR_VALID_MIN_A <= current <= SENSOR_VALID_MAX_A:
            problem = (
                "Sensorsignal außerhalb des Softwarebereichs "
                f"{SENSOR_VALID_MIN_A * 1000:.1f}–"
                f"{SENSOR_VALID_MAX_A * 1000:.1f} mA"
            ).replace(".", ",")
        else:
            problem = None
        readings[name] = SensorReading(current, received_at, problem)
    return readings


# Der Worker übergibt seinen Status als Daten an die Oberfläche.
# Booleans und Task-Namen dokumentieren Ressourcenbesitz der Software.
# Kein Feld behauptet eine sichere Anlage oder gemessenen Ausgangsstrom.
@dataclass(frozen=True)
class DaqState:
    measurement_task: bool
    output_tasks: tuple[str, ...]
    output_attempted: bool
    output_fault: bool
