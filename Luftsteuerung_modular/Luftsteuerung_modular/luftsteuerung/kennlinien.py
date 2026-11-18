"""Eingaben prüfen und Prozent, Strom sowie Durchfluss umrechnen.

Eine Kennlinie beschreibt, welcher Strom zu welchem Prozentwert gehört.
Die Sensorformeln rechnen dagegen einen gemessenen Strom in Durchfluss um.
Diese Aufgaben bleiben getrennt. Keine Funktion dieser Datei öffnet NI-Tasks
oder verändert Widgets. Fehlerarten, Meldungen und Rechenreihenfolgen bleiben
bis auf die ausdrücklich dokumentierte Zusammenfassung des GLF-/GHF-Rechenwegs
wie im Original. Auch dort bleibt das Rechenergebnis gleich.

CONFIGURED_ACTUATOR_CURVES ist das gemeinsame Verzeichnis der vorhandenen
Stellgliedfunktionen. Die Definition und Registrierung stehen zusammen in
dieser Datei; dadurch entsteht kein gegenseitiger Import mit der Hardware.
"""

from __future__ import annotations

import math
from typing import Callable

from .konfiguration import (
    GHF_REFERENCE_M3H,
    GHF_SENSOR_FACTOR,
    GLF_REFERENCE_M3H,
    GLF_SENSOR_FACTOR,
    MAX_OUTPUT_A,
    MIN_OUTPUT_A,
    SENSOR_ZERO_A,
)


# Die Stellgliedkennlinien werden ausdrücklich getrennt von den Sensorformeln geführt.
# Tatsächlich ausgeführte Kennlinien des Originals:
# FU: 0–100 % = 4–20 mA. GLF/GHF: 0–100 % = 20–4 mA.
# Die ältere Kommentarangabe 0–20 mA widersprach den vorhandenen Formeln.
# Die Formeln bleiben erhalten; dies ist keine neue fachliche Bestätigung.
# Kennlinienfunktionen rechnen Prozent -> Ampere und haben keine Hardware-Seiteneffekte.
CONFIGURED_ACTUATOR_CURVES: dict[str, Callable[[float], float]] = {}


# Prüft, ob ein Prozentwert zwischen 0 und 100 liegt. Auch NaN und True/False sind ungültig.
def validate_percent(value: float, name: str) -> float:
    """Eine endliche Zahl einschließlich 0 und 100 Prozent verlangen."""
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not 0 <= value <= 100):
        raise ValueError(f"{name}: Nur Werte von 0 bis 100 % zulässig.")
    return float(value)


# Liest einen Prozentwert aus dem Eingabefeld. Leer bedeutet: Diesen Kanal nicht ändern.
def parse_percent(text: str, name: str) -> float | None:
    """Ein leeres Feld ist 'unverändert'; Dezimalkommas sind erlaubt."""
    cleaned = text.strip()
    if not cleaned:
        return None
    try:
        value = float(cleaned.replace(",", "."))
    except ValueError as exc:
        raise ValueError(f"{name}: Bitte eine gültige Zahl eingeben.") from exc
    return validate_percent(value, name)


# Prüft, ob der Stromwert in der konfigurierten Spanne von 0 bis 20 mA liegt.
def validate_current(current_a: float) -> float:
    """Den NI-9266-Ausgangsbereich ohne NaN oder Infinity erzwingen."""
    if (isinstance(current_a, bool) or not isinstance(current_a, (int, float))
            or not math.isfinite(current_a)
            or not MIN_OUTPUT_A <= current_a <= MAX_OUTPUT_A):
        raise ValueError(
            f"Strom-Sollwert muss zwischen {MIN_OUTPUT_A * 1000:g} mA "
            f"und {MAX_OUTPUT_A * 1000:g} mA liegen."
        )
    return float(current_a)


# Rechnet den FU-Sollwert um: 0 % = 4 mA, 100 % = 20 mA.
def fu_percent_to_amp(percent: float) -> float:
    """Bekannte FU-Skalierung: 0 % = 4 mA und 100 % = 20 mA."""
    percent = validate_percent(percent, "FU")
    return validate_current(SENSOR_ZERO_A + percent / 100 * 0.016)


# Gemeinsamer Rechenweg der beiden Gleitschieber. Der Kanalname bleibt ein
# Parameter, damit eine ungültige GLF-Eingabe weiterhin GLF in der Meldung nennt
# und eine ungültige GHF-Eingabe weiterhin GHF. Reihenfolge und Formel stammen
# unverändert aus den beiden bisherigen Funktionen.
def _gleitschieber_percent_to_amp(percent: float, name: str) -> float:
    """Den identischen GLF-/GHF-Rechenweg genau einmal ausführen.

    Zuerst wird der Prozentwert geprüft, dann der Strom berechnet und geprüft.
    0 % ergeben 0.020 A, 50 % ergeben 0.012 A und 100 % ergeben 0.004 A.
    Diese Funktion berechnet nur; sie sendet keinen Hardwarebefehl.
    """
    percent = validate_percent(percent, name)
    current_a = MAX_OUTPUT_A - percent / 100 * (
        MAX_OUTPUT_A - SENSOR_ZERO_A
    )
    return validate_current(current_a)


# Umgekehrte GLF-Kennlinie:
# 0 % = 20 mA und 100 % = 4 mA.
def glf_percent_to_amp(percent: float) -> float:
    """GLF-Sollwert umgekehrt linear von 20 mA auf 4 mA skalieren."""
    # Der bisherige Funktionsname bleibt für alle Aufrufer erhalten.
    return _gleitschieber_percent_to_amp(percent, "GLF")


# Umgekehrte GHF-Kennlinie:
# 0 % = 20 mA und 100 % = 4 mA.
def ghf_percent_to_amp(percent: float) -> float:
    """GHF-Sollwert umgekehrt linear von 20 mA auf 4 mA skalieren."""
    # Gleiche Rechnung wie GLF, aber unverändert eigener Name in Fehlermeldungen.
    return _gleitschieber_percent_to_amp(percent, "GHF")

# Berechnet den GHF-Durchfluss aus dem Sensorstrom. Das ist keine Schieberposition.
def ghf_amp_to_m3h(current_a: float) -> float:
    """GHF-SENSORstrom in Durchfluss umrechnen (nicht Gleitschieberposition)."""
    return (current_a - SENSOR_ZERO_A) / GHF_SENSOR_FACTOR


# Berechnet den GLF-Durchfluss aus dem Sensorstrom. Das ist keine Schieberposition.
def glf_amp_to_m3h(current_a: float) -> float:
    """GLF-SENSORstrom in Durchfluss umrechnen (nicht Gleitschieberposition)."""
    return (current_a - SENSOR_ZERO_A) / GLF_SENSOR_FACTOR


# Rechnet den FU-Strom in einen Prozentwert zurück. Das ist keine echte Rückmessung.
def fu_amp_to_percent(current_a: float) -> float:
    """Umkehrfunktion der vorgegebenen FU-Sollwert-Skalierung."""
    return (current_a - SENSOR_ZERO_A) / 0.016 * 100


# Alte Bezugsrechnung für den GHF-Sensor. Nicht für die Stellgliedausgabe verwenden.
def ghf_reference_percent_to_amp(percent: float) -> float:
    """Alte GHF-Formel: % des Durchfluss-Bezugswerts -> Sensorstrom.

    NICHT als Stellstrom für den GHF-Gleitschieber verwenden!
    """
    percent = validate_percent(percent, "GHF-Durchflussbezug")
    return SENSOR_ZERO_A + percent / 100 * GHF_REFERENCE_M3H * GHF_SENSOR_FACTOR


# Alte Bezugsrechnung für den GLF-Sensor. Nicht für die Stellgliedausgabe verwenden.
def glf_reference_percent_to_amp(percent: float) -> float:
    """Alte GLF-Formel: % des Durchfluss-Bezugswerts -> Sensorstrom.

    NICHT als Stellstrom für den GLF-Gleitschieber verwenden!
    """
    percent = validate_percent(percent, "GLF-Durchflussbezug")
    return SENSOR_ZERO_A + percent / 100 * GLF_REFERENCE_M3H * GLF_SENSOR_FACTOR


# Vergleicht den GHF-Durchfluss mit dem Bezugswert von 449 m³/h.
def ghf_amp_to_reference_percent(current_a: float) -> float:
    """GHF-Durchfluss in % von 449 m³/h (nicht Schieberöffnung)."""
    return ghf_amp_to_m3h(current_a) / GHF_REFERENCE_M3H * 100


# Vergleicht den GLF-Durchfluss mit dem Bezugswert von 100 m³/h.
def glf_amp_to_reference_percent(current_a: float) -> float:
    """GLF-Durchfluss in % von 100 m³/h (nicht Schieberöffnung)."""
    return glf_amp_to_m3h(current_a) / GLF_REFERENCE_M3H * 100


# Prüft alle ausgefüllten Eingaben vor der Ausgabe und berechnet die Sollströme.
def prepare_commands(raw_entries: dict[str, str]) -> list[tuple[str, float, float]]:
    """Alle Eingaben VOR dem ersten DAQ-Write prüfen.

    Ergebnis: [(Gerät, Prozent, Strom_in_A), ...].
    Ein fehlender/gesperrter Kanal stoppt die gesamte Übernahme.
    """
    # Hier werden nur validierte Absichten gesammelt, noch keine Befehle ausgeführt.
    commands = []
    for name in ("FU", "GLF", "GHF"):
        value = parse_percent(raw_entries.get(name, ""), name)
        if value is None:
            continue
        # Keine Kennlinie durch einen geratenen Standard oder eine Sensorformel ersetzen.
        curve = CONFIGURED_ACTUATOR_CURVES.get(name)
        if curve is None:
            raise RuntimeError(
                f"{name}: Reale Ausgabe gesperrt – Stellgliedkennlinie nicht bestätigt."
            )
        commands.append((name, value, validate_current(curve(value))))
    return commands


# Die drei im Original hinterlegten Kennlinien zentral registrieren. Diese Registrierung
# aktiviert die Eingabefelder und erlaubt dem Worker die jeweilige Ausgabe.
# Der vorhandene Bestätigungsdialog für manuelle Aufträge bleibt erhalten.
# Die unveränderte Startinitialisierung des Workers läuft separat.
# Das Wörterbuch existiert nur hier. Andere Module importieren dasselbe
# Objekt; es wird keine zweite, möglicherweise abweichende Kopie angelegt.
CONFIGURED_ACTUATOR_CURVES["FU"] = fu_percent_to_amp
CONFIGURED_ACTUATOR_CURVES["GLF"] = glf_percent_to_amp
CONFIGURED_ACTUATOR_CURVES["GHF"] = ghf_percent_to_amp
