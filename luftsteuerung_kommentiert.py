"""Nassheuer-Luftstrecke | Manuelle Steuerung und Strommessung.

Programmzweck
--------------
- Zwei analoge Sensorsignale (NI-9253) kontinuierlich auslesen.
- Den FU manuell über den NI-9266 ansteuern (nach Bedienerfreigabe).
- FU, GLF und GHF nach Bedienerbestätigung manuell ansteuern.
- GLF/GHF verwenden die bestätigte lineare Kennlinie 0–100 % = 0–20 mA.

SICHERHEIT
----------
Dies ist eine Bedienoberfläche, keine sicherheitsgerichtete Steuerung.
Weder ein erfolgreicher DAQ-Schreibaufruf noch task.close() beweisen einen
sicheren Anlagenzustand. Keine Ausgaben beim Programmstart oder beim Schließen.
Vor dem realen Einsatz: NI-MAX-Adressen, Verdrahtung, Freigabe und eine
unabhängige Abschaltmöglichkeit durch Verantwortliche prüfen lassen.
"""

from __future__ import annotations

# Abhängigkeiten und Verantwortungen:
# math: endliche Zahlen, Kennlinien und ausschließlich simulierte Signalverläufe.
# time.monotonic: Messalter/Zeitabläufe unabhängig von Systemuhr oder Zeitzone.
# argparse: ausdrückliche Betriebsmoduswahl am Programmeinstieg.
# queue/threading: Datenübergabe und Ressourcenbesitz zwischen Tk und DAQ-Thread.
# datetime: lesbare Uhrzeit im Ereignisprotokoll, niemals Messfrische.
# customtkinter: vorhandenes Tkinter-basiertes GUI-Framework.
# NI-DAQmx fehlt absichtlich hier: reine Simulation benötigt das Paket nicht.
import math
import time
import argparse
import queue
import threading
from dataclasses import dataclass
from datetime import datetime
from tkinter import messagebox
from typing import Callable

import customtkinter as ctk


# ============================================================================
# 1 | KONFIGURATION – HIER STEHEN ALLE ANLAGENSPEZIFISCHEN WERTE
# ============================================================================
# Die Gerätebezeichnungen stammen aus der bereitgestellten Bedienungsanleitung.
# Unbedingt mit NI-MAX und der tatsächlichen Verdrahtung vergleichen.
# Namen aus dem vorhandenen Projektcode unverändert übernommen.
# Die im Altkommentar genannte Bedienungsanleitung ist hier kein geprüfter Nachweis.
# Gerätekennung, Modulposition und Klemmen vor realem Einsatz vor Ort bestätigen.
DEVICE = "cDAQ9183-251EC5D"
# Explizite Ausgangszuordnung statt Annahme aus der Reihenfolge von GUI-Karten.
# FU -> ao0, GLF -> ao1, GHF -> ao2; Task-Konfiguration und Anzeige teilen die Quelle.
AO_CHANNELS = {
    "FU": f"{DEVICE}Mod1/ao0",
    "GLF": f"{DEVICE}Mod1/ao1",
    "GHF": f"{DEVICE}Mod1/ao2",
}
# Eingangskanäle haben einen eigenen Vertrag: Rückgabe stets (GHF/ai0, GLF/ai1).
AI_GHF = f"{DEVICE}Mod2/ai0"
AI_GLF = f"{DEVICE}Mod2/ai1"
# Reihenfolge stimmt mit dem gemeinsamen NI-Mess-Task überein. Die Bewertung
# und Darstellung erfolgen trotzdem unabhängig für jede einzelne Messstelle.
SENSOR_CHANNELS = {"GHF": AI_GHF, "GLF": AI_GLF}

# Verschiedene Begriffe nicht vermischen: DAQ-Konfigurationsbereich,
# erwarteter Sensorbereich, Stellgliedkennlinie und tatsächlicher Anlagenzustand.
# Die Konstanten sind übernommene Vorgaben, keine neu bestätigten Modulgrenzen.
MIN_OUTPUT_A = 0.0
MAX_OUTPUT_A = 0.020              # NI-9266: 0–20 mA
SENSOR_ZERO_A = 0.004            # Sensor-Bezugspunkt: 4 mA
SENSOR_MAX_A = 0.020             # Erwartetes oberes Sensorsignal
# Toleranz ausschließlich für die Plausibilitätsprüfung realer Messwerte.
# Die physikalische Umrechnung behält 4,000 mA als Durchfluss-Nullpunkt.
SENSOR_VALID_MIN_A = 0.0039       # 3,9 mA: untere akzeptierte Messtoleranz
SENSOR_VALID_MAX_A = 0.0201       # 20,1 mA: obere akzeptierte Messtoleranz

# Dimensionsprüfung: (A - A) / (A je m³/h) ergibt m³/h.
# Sensorfaktoren dürfen keine Aktuatorkennlinie allein aufgrund gleicher Einheit ersetzen.
GHF_SENSOR_FACTOR = 0.0000356   # A je m³/h; vorhandene Messformel
GLF_SENSOR_FACTOR = 0.00016     # A je m³/h; vorhandene Messformel
GHF_REFERENCE_M3H = 449.0       # Bezugswert der alten Durchfluss-Prozentfunktion
GLF_REFERENCE_M3H = 100.0       # Bezugswert der alten Durchfluss-Prozentfunktion

# Erfassung und Anzeige besitzen verschiedene Takte: kontinuierliche Samples im
# Task, gemittelte Pakete für die GUI. Frischegrenze ist nur Software-Plausibilität.
SAMPLE_RATE_HZ = 1000           # NI-9253: 1000 Samples/s und Kanal
GUI_REFRESH_MS = 200            # GUI wird ca. 5-mal/s aktualisiert
MAX_MEASUREMENT_AGE_S = 1.0    # Nur Software-Plausibilität, keine Safety-Funktion

# Die Stellgliedkennlinien werden ausdrücklich getrennt von den Sensorformeln geführt.
# FU: 0–100 % = 4–20 mA. GLF/GHF: 0–100 % = 0–20 mA.
# Die linearen GLF/GHF-Kennlinien wurden vom Benutzer am 3. November 2026 bestätigt.
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
        raise ValueError("Strom-Sollwert muss zwischen 0 und 20 mA liegen.")
    return float(current_a)


# Rechnet den FU-Sollwert um: 0 % = 4 mA, 100 % = 20 mA.
def fu_percent_to_amp(percent: float) -> float:
    """Bekannte FU-Skalierung: 0 % = 4 mA und 100 % = 20 mA."""
    percent = validate_percent(percent, "FU")
    return validate_current(SENSOR_ZERO_A + percent / 100 * 0.016)


# Rechnet den GLF-Sollwert um: 0 % = 0 mA, 100 % = 20 mA.
def glf_percent_to_amp(percent: float) -> float:
    """Bestätigte GLF-Skalierung: 0 % = 0 mA und 100 % = 20 mA."""
    percent = validate_percent(percent, "GLF")
    return validate_current(percent / 100 * MAX_OUTPUT_A)


# Rechnet den GHF-Sollwert um: 0 % = 0 mA, 100 % = 20 mA.
def ghf_percent_to_amp(percent: float) -> float:
    """Bestätigte GHF-Skalierung: 0 % = 0 mA und 100 % = 20 mA."""
    percent = validate_percent(percent, "GHF")
    return validate_current(percent / 100 * MAX_OUTPUT_A)


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


# Alle drei bestätigten Kennlinien zentral registrieren. Diese Registrierung
# aktiviert die Eingabefelder und erlaubt dem Worker die jeweilige Ausgabe.
# Eine betriebliche Anlagenfreigabe und der Bestätigungsdialog bleiben erforderlich.
CONFIGURED_ACTUATOR_CURVES["FU"] = fu_percent_to_amp
CONFIGURED_ACTUATOR_CURVES["GLF"] = glf_percent_to_amp
CONFIGURED_ACTUATOR_CURVES["GHF"] = ghf_percent_to_amp


# Bündelt echte NI-DAQ-Aufrufe und verwaltet Mess- und Ausgangs-Tasks.
class DaqController:
    """Besitzt den Mess-Task und die unabhängigen Ausgangs-Tasks.

    Die Klasse erstellt beim Initialisieren KEINE NI-DAQ-Tasks.
    Sie gibt niemals eigenständig einen Abschaltstrom aus.
    """

    # Merkt sich den Treiber, die Tasks und den Ausgabestatus. Legt noch keinen Task an.
    def __init__(self, driver=None) -> None:
        # Testtreiber und echter Treiber erfüllen denselben aufrufbaren Adaptervertrag.
        self.driver = driver
        self.measurement_task = None
        self.output_tasks: dict[str, object] = {}
        # Nur bestätigte Sollströme speichern. Ein späterer Fehler kann den realen
        # Ausgang trotzdem unbekannt machen; diese Historie ist keine Rückmessung.
        self.last_written: dict[str, float] = {}
        # Ein Versuch bleibt bis Programmende erinnerbar, auch nach Task-Freigabe.
        self.output_attempted = False
        # Verriegelung ohne automatischen Reset: Teilaufträge nie blind wiederholen.
        self.output_fault = False

    # Richtet beide Messkanäle in einem Task ein und startet die Erfassung.
    def start_measurement(self) -> None:
        """Zwei Strommesseingänge in einem kontinuierlichen Task starten."""
        if self.measurement_task is not None:
            raise RuntimeError("Der Mess-Task existiert bereits.")

        # Verzögerter Import hält die Demonstration ohne installierte NI-Software möglich.
        if self.driver is None:
            try:
                import nidaqmx
            except ImportError as exc:
                raise RuntimeError("NI-DAQmx-Pythonpaket fehlt. Siehe README (Hardwarebetrieb).") from exc
            self.driver = nidaqmx
        # Ab jetzt existiert eine Ressource, auch wenn Konfiguration/Start noch scheitert.
        task = self.driver.Task()
        try:
            # Kanalreihenfolge bestimmt später data[0] und data[1].
            task.ai_channels.add_ai_current_chan(
                AI_GHF, min_val=-0.020, max_val=0.020
            )
            task.ai_channels.add_ai_current_chan(
                AI_GLF, min_val=-0.020, max_val=0.020
            )
            # Beide Kanäle teilen denselben kontinuierlichen Messauftrag.
            task.timing.cfg_samp_clk_timing(
                rate=SAMPLE_RATE_HZ,
                sample_mode=self.driver.constants.AcquisitionType.CONTINUOUS,
            )
            task.start()
        except Exception:
            try:
                task.close()
            except Exception:
                # Referenz behalten, wenn das Schließen misslingt.
                self.measurement_task = task
            raise
        self.measurement_task = task

    # Liest neue Samples und bildet je Kanal den Mittelwert in Ampere.
    def read_currents(self) -> tuple[float | None, float | None] | None:
        """Verfügbare Samples mitteln; Rückgabe in Ampere (GHF, GLF)."""
        if self.measurement_task is None:
            raise RuntimeError("Es existiert kein laufender Mess-Task.")

        data = self.measurement_task.read(
            number_of_samples_per_channel=self.driver.constants.READ_ALL_AVAILABLE,
            timeout=0.5,
        )
        if len(data) == 0:
            return None  # Direkt nach Task-Start kann der Puffer leer sein.
        # Abweichende Datenstruktur darf keine stillschweigende Kanalvertauschung ergeben.
        if len(data) != 2:
            raise RuntimeError("DAQ lieferte nicht die erwarteten zwei Kanäle.")
        # Ein leerer Kanal wird unten separat als fehlend markiert. Zwei
        # nichtleere, unterschiedlich lange Puffer bleiben dagegen ein gemeinsamer
        # Strukturfehler des synchron angeforderten Zweikanal-Tasks.
        if data[0] and data[1] and len(data[0]) != len(data[1]):
            raise RuntimeError("DAQ lieferte unterschiedlich lange Kanalpuffer.")
        # Ein fehlender oder ungültiger Kanal darf den anderen nicht verwerfen.
        # None bedeutet fehlende Samples; NaN bedeutet ungültige Samples. Echte
        # Task.read()-Fehler und eine falsche Kanalanzahl bleiben gemeinsame Fehler.
        def average_channel(samples) -> float | None:
            if not samples:
                return None
            if any(not math.isfinite(value) for value in samples):
                return math.nan
            return sum(samples) / len(samples)

        # Die Rohwerte bleiben Ampere, einschließlich endlicher Werte außerhalb
        # 4–20 mA. Nur so kann die GUI z. B. 0,000 mA als Fehlerwert anzeigen.
        return average_channel(data[0]), average_channel(data[1])

    # Gibt den geprüften Sollstrom auf dem benannten Kanal aus und meldet Fehler.
    def write_current(self, name: str, current_a: float) -> None:
        """Einen explizit freigegebenen NI-9266-Kanal beschreiben.

        Ein erfolgreicher write() beweist NICHT den real anliegenden Strom.
        """
        if name not in AO_CHANNELS:
            raise ValueError(f"Unbekannter Ausgang: {name}")
        if name not in CONFIGURED_ACTUATOR_CURVES:
            raise RuntimeError(f"{name}: Keine bestätigte Stellgliedkennlinie.")
        if self.output_fault:
            raise RuntimeError("Ausgabe nach DAQ-Fehler gesperrt.")
        current_a = validate_current(current_a)

        # Diesen Zustand vor dem ersten möglichen Hardwareaufruf festhalten.
        self.output_attempted = True
        # Pro Ausgang einen On-Demand-Task wiederverwenden statt bei jedem Klick reservieren.
        task = self.output_tasks.get(name)
        newly_created = task is None

        try:
            if newly_created:
                if self.driver is None:
                    raise RuntimeError("DAQ-Treiber nicht initialisiert.")
                task = self.driver.Task()
                # Task sofort referenzieren, auch falls die Konfiguration fehlschlägt.
                self.output_tasks[name] = task
                task.ao_channels.add_ao_current_chan(
                    AO_CHANNELS[name], min_val=MIN_OUTPUT_A, max_val=MAX_OUTPUT_A
                )

            # Ein Kanal, ein Sample: softwaregetaktete On-Demand-Ausgabe.
            count = task.write(current_a, auto_start=True, timeout=5.0)
            if count != 1:
                raise RuntimeError(f"{name}: DAQ bestätigte nicht genau ein Sample.")
            # Erst nach Bestätigung genau eines Samples den Sollstrom als gemeldet speichern.
            self.last_written[name] = current_a

        except Exception:
            self.output_fault = True
            # Nur einen gerade neu angelegten, fehlgeschlagenen Task schließen.
            # Bereits erfolgreich konfigurierte Kanäle nicht still verändern.
            if newly_created and task is not None:
                try:
                    task.close()
                except Exception:
                    # Bei Schließfehler Referenz für on_close() behalten.
                    pass
                else:
                    self.output_tasks.pop(name, None)
            raise

    # Schließt nur den Mess-Task, etwa nach einem Lesefehler.
    def close_measurement(self) -> list[str]:
        """Nur den Mess-Task nach einem Lesefehler freigeben.

        Die Ausgabe-Tasks werden NICHT angefasst. Ein Fehler bleibt sichtbar.
        """
        if self.measurement_task is None:
            return []
        try:
            self.measurement_task.close()
        except Exception as exc:
            return [str(exc)]
        self.measurement_task = None
        return []

    # Versucht alle bekannten Tasks zu schließen. Schreibt dabei keinen Abschaltstrom.
    def close_tasks(self) -> list[str]:
        """Alle bekannten Tasks schließen; Fehler sammeln statt verschlucken.

        Schreibt ausdrücklich KEINEN neuen Stromwert.
        """
        problems: list[str] = []
        # Kopie erlaubt Entfernen erfolgreicher Einträge während des Durchlaufs.
        # Ein defekter Kanal verhindert das Aufräumen anderer Ressourcen nicht.
        for name, task in list(self.output_tasks.items()):
            try:
                task.close()
            except Exception as exc:
                problems.append(f"{name}-Ausgang: {exc}")
            else:
                del self.output_tasks[name]

        if self.measurement_task is not None:
            try:
                self.measurement_task.close()
            except Exception as exc:
                problems.append(f"Messung: {exc}")
            else:
                self.measurement_task = None

        if problems:
            self.output_fault = True
        return problems


# Ersetzt NI-Hardware durch Beispieldaten. Es werden keine echten Ausgänge angesprochen.
class SimulatedDaqController(DaqController):
    """Deterministische Demonstration; importiert und verwendet kein NI-DAQmx.

    Sensorsignale sind unabhängige Beispieldaten, kein physikalisches Anlagenmodell.
    Die Simulation verwendet dieselben bestätigten Kennlinien und schreibt nie
    auf reale NI-Hardware.
    """

    # Übernimmt den Softwarezustand, ohne NI-Hardware anzusprechen.
    def __init__(self) -> None:
        super().__init__()
        self.started_at = 0.0

    # Markiert die simulierte Messung als gestartet.
    def start_measurement(self) -> None:
        if self.measurement_task is not None:
            raise RuntimeError("Der simulierte Mess-Task existiert bereits.")
        # Reiner Existenzmarker ohne Treibermethoden; Close ist deshalb ebenfalls überschrieben.
        self.measurement_task = object()
        self.started_at = time.monotonic()

    # Liefert zwei leicht schwankende Beispielströme.
    def read_currents(self) -> tuple[float, float]:
        if self.measurement_task is None:
            raise RuntimeError("Simulierte Messung nicht gestartet.")
        phase = (time.monotonic() - self.started_at) / 8
        return (0.012 + math.sin(phase) * 0.0003,
                0.010 + math.cos(phase) * 0.0002)

    # Merkt sich einen simulierten Stellbefehl, ohne Strom auszugeben.
    def write_current(self, name: str, current_a: float) -> None:
        if name not in AO_CHANNELS or name not in CONFIGURED_ACTUATOR_CURVES:
            raise RuntimeError(f"{name}: Keine freigegebene Kennlinie.")
        if self.output_fault:
            raise RuntimeError("Simulierte Ausgabe nach Fehler gesperrt.")
        current_a = validate_current(current_a)
        self.output_attempted = True
        # Wiederverwendung eines simulierten Ausgangs abbilden, keine echte Kanalreservierung.
        self.output_tasks.setdefault(name, object())
        self.last_written[name] = current_a

    # Entfernt den Marker der simulierten Messung.
    def close_measurement(self) -> list[str]:
        self.measurement_task = None
        return []

    # Entfernt alle simulierten Task-Marker.
    def close_tasks(self) -> list[str]:
        self.output_tasks.clear()
        self.measurement_task = None
        return []


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
        return (self.problem is None and self.current_a is not None
                and math.isfinite(self.current_a)
                and SENSOR_VALID_MIN_A <= self.current_a <= SENSOR_VALID_MAX_A)

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
            problem = "Sensorsignal außerhalb 3,9–20,1 mA"
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


# Führt Messung und Ausgaben außerhalb des GUI-Threads aus.
class DaqWorker(threading.Thread):
    """Einziger Besitzer der DAQ-Tasks; GUI erhält nur unveränderliche Ereignisse.

    Schließanforderungen sperren neue Writes sofort. Ein bereits laufender
    Treiberaufruf ist nicht abbrechbar; die GUI wartet auf dessen Abschluss.
    """

    # Erstellt die Queues und Zustandsvariablen für den Worker.
    def __init__(self, controller: DaqController) -> None:
        super().__init__(name="Luftsteuerung-DAQ", daemon=False)
        self.controller = controller
        # Ereignisformat jeweils (Art, Nutzdaten), Richtung Worker -> Tk:
        # measurement: (dict[str, SensorReading], monotone_Lesezeit)
        # Jede SensorReading enthält ihren eigenen Rohwert und Fehlerstatus.
        self.events: queue.Queue = queue.Queue()
        # Befehle Tk -> Worker: „write“ oder „close“. Keine Widgets in Nutzdaten ablegen.
        self.commands: queue.Queue = queue.Queue()
        # Sofortige Sperrabsicht mit Vorrang vor der Queue-Position des close-Befehls.
        self.close_requested = threading.Event()
        self.last_valid_at = 0.0
        self.measurement_valid = False
        self.sensor_readings = {name: SensorReading() for name in SENSOR_CHANNELS}
        self.measurement_running = False
        self.shutdown_blocked = False
        # Nur kurze Auftragsannahme schützen, keinen langsamen Treiberaufruf darunter halten.
        self.accepted_lock = threading.Lock()
        self.write_pending = False

    # Erstellt einen Status-Schnappschuss ohne direkte Task-Objekte.
    def state(self) -> DaqState:
        return DaqState(self.controller.measurement_task is not None,
                        tuple(self.controller.output_tasks),
                        self.controller.output_attempted, self.controller.output_fault)

    # Nimmt einen Stellauftrag an, wenn noch keiner läuft und kein Schließen geplant ist.
    def request_write(self, commands: list[tuple[str, float, float]]) -> bool:
        with self.accepted_lock:
            if self.close_requested.is_set() or self.write_pending:
                return False
            self.write_pending = True
        self.commands.put(("write", tuple(commands)))
        return True

    # Sperrt neue Aufträge und fordert das Schließen aller Tasks an.
    def request_close(self) -> None:
        self.close_requested.set()
        self.commands.put(("close", ()))

    # Prüft, ob ein gültiger Messwert höchstens eine Sekunde alt ist.
    def _recent(self) -> bool:
        return (self.measurement_valid and
                time.monotonic() - self.last_valid_at <= MAX_MEASUREMENT_AGE_S)

    # Liest Sensorströme und sendet Messwerte oder Fehlermeldungen zur GUI.
    def _read(self) -> None:
        try:
            currents = self.controller.read_currents()
            # Zeit im Besitzerthread erfassen, nicht erst bei späterer GUI-Verarbeitung.
            acquired_at = time.monotonic()
            self.sensor_readings = evaluate_sensor_currents(currents, acquired_at)
            # Der gemeinsame Zustand dient ausschließlich der bisherigen
            # Ausgabefreigabe. Die Messkarten verwenden ihre eigenen Zustände.
            self.measurement_valid = all(reading.valid for reading in self.sensor_readings.values())
            if self.measurement_valid:
                self.last_valid_at = acquired_at
            # Auch fehlerhafte/fehlende Rohwerte mit ihrem Empfangszeitpunkt an
            # die GUI weitergeben. Kein Kanal wird wegen des anderen ausgeblendet.
            self.events.put(("measurement", (self.sensor_readings.copy(), acquired_at)))
        except Exception as exc:
            self.measurement_valid = False
            self.sensor_readings = {name: SensorReading(problem="DAQ-Messfehler")
                                    for name in SENSOR_CHANNELS}
            self.measurement_running = False
            # Erfassung beenden, Ausgänge erhalten; fehlgeschlossene Task-Referenzen bleiben.
            problems = self.controller.close_measurement()
            self.events.put(("measurement_error", str(exc) +
                             (" | Schließfehler: " + "; ".join(problems) if problems else "")))
            self.events.put(("state", self.state()))

    # Prüft einen Stellauftrag erneut und schreibt die Kanäle nacheinander.
    def _write(self, commands) -> None:
        successful = []
        try:
            if not commands:
                raise ValueError("Leerer Ausgabeauftrag.")
            # Auch interne Aufträge vollständig prüfen, bevor ein Write beginnt.
            for name, percent, current in commands:
                if name not in CONFIGURED_ACTUATOR_CURVES:
                    raise RuntimeError(f"{name}: Stellgliedkennlinie fehlt.")
                # Interner Prozentwert und übergebener Strom müssen derselben Kennlinie folgen.
                expected = validate_current(CONFIGURED_ACTUATOR_CURVES[name](
                    validate_percent(percent, name)))
                if validate_current(current) != expected:
                    raise ValueError(f"{name}: Strom passt nicht zur Kennlinie.")
            for name, percent, current in commands:
                if (self.close_requested.is_set() or self.shutdown_blocked
                        or self.controller.output_fault or not self._recent()):
                    raise RuntimeError("Ausgabe gesperrt: Messung veraltet/ungültig oder Abschluss aktiv.")
                # Direkt davor erneut Frische und Abschlussabsicht prüfen.
                self.controller.write_current(name, current)
                # Nur zurückgekehrte bestätigte Teilbefehle in die Erfolgsliste aufnehmen.
                successful.append((name, percent, current))
            self.events.put(("write_ok", tuple(successful)))
        except Exception as exc:
            # Auch ein teilweise ausgeführter Auftrag wird nie automatisch wiederholt.
            self.controller.output_fault = True
            self.events.put(("write_error", (str(exc), tuple(successful))))
        finally:
            # Reservierung in jedem Ausgangspfad aufheben; Fehlersperre bleibt erhalten.
            with self.accepted_lock:
                self.write_pending = False
            self.events.put(("state", self.state()))

    # Startet die Messung, bearbeitet Aufträge und schließt die Tasks auf Anforderung.
    def run(self) -> None:
        if not self.close_requested.is_set():
            try:
                self.controller.start_measurement()
                self.measurement_running = True
                self.events.put(("started", None))
            except Exception as exc:
                self.events.put(("measurement_error", f"Initialisierung fehlgeschlagen: {exc}"))
        self.events.put(("state", self.state()))
        next_read = time.monotonic()
        while True:
            # Abschluss hat Vorrang: Wiederanlauf sperren, dann Ressourcen freigeben.
            if self.close_requested.is_set() and not self.shutdown_blocked:
                self.shutdown_blocked = True
                self.measurement_running = False
                self.measurement_valid = False
                problems = self.controller.close_tasks()
                self.events.put(("state", self.state()))
                if not problems:
                    self.events.put(("closed", ()))
                    return
                # Nach Fehler nur erneute Freigabe derselben Ressourcen zulassen.
                # Ein wartender Write wird verworfen, kein Wiedereinstieg in Messung.
                while True:
                    try:
                        action, _ = self.commands.get_nowait()
                    except queue.Empty:
                        break
                # Erst nach dem Verwerfen alter Aufträge eine Wiederholung anbieten.
                # Andernfalls könnte die GUI bereits einen neuen Close-Auftrag senden,
                self.events.put(("closed", tuple(problems)))
                continue
            try:
                # Kurzes Warten vermeidet dauerhaftes Busy-Waiting bei leerer Befehlsqueue.
                action, payload = self.commands.get(timeout=0.02)
            except queue.Empty:
                action = None
            # Expliziter Wiederholungsversuch betrifft nur noch nicht freigegebene Ressourcen.
            if action == "close" and self.shutdown_blocked:
                problems = self.controller.close_tasks()
                self.events.put(("state", self.state()))
                self.events.put(("closed", tuple(problems)))
                if not problems:
                    return
            elif action == "write" and not self.close_requested.is_set():
                # Vor jedem Auftrag nochmals aktuelle Samples holen.
                if self.measurement_running:
                    self._read()
                self._write(payload)
            now = time.monotonic()
            if self.measurement_running and not self.close_requested.is_set() and now >= next_read:
                self._read()
                # Keine aufgestauten GUI-Leseaufrufe nachholen; neues Intervall ab Leseende.
                next_read = time.monotonic() + GUI_REFRESH_MS / 1000


# ============================================================================
# 5 | OBERFLÄCHE – SOLLWERTE, MESSWERTE UND BESTÄTIGUNGEN GETRENNT
# ============================================================================
# Ruhige HMI-Palette: Petrol für Bedienung, Grün für plausible reale Messdaten,
# Orange für unklare/gesperrte Zustände und Rot für Fehler. Simulation bleibt blau.
# Jeder bedeutsame Zustand wird zusätzlich mit verständlichem Text bezeichnet.
COLOR_DARK = "#142D3B"
COLOR_BLUE = "#126B80"
COLOR_GREEN = "#167450"
COLOR_RED = "#B42332"
COLOR_ORANGE = "#986000"
TEXT_MUTED = "#536979"
CARD_BG = "#FFFFFF"
SUBTLE_BG = "#EAF0F4"


# Formatiert Zahlen mit deutschem Dezimalkomma, ohne ihre Werte zu ändern.
def number_de(value: float, digits: int = 2) -> str:
    """Deutsches Dezimalformat ohne globale Locale-Änderung."""
    return f"{value:.{digits}f}".replace(".", ",")


# Erstellt die Oberfläche und verarbeitet Benutzereingaben sowie Worker-Meldungen.
class LuftsteuerungApp(ctk.CTk):
    """Tk-Hauptthread zeichnet ausschließlich und verarbeitet Bedienaktionen."""

    # Richtet das Fenster ein, baut die Oberfläche auf und startet den Worker.
    def __init__(self, *, simulate: bool = True, controller=None) -> None:
        # Auch bei programmatischem Aufruf darf ein echter Controller niemals
        # unter einer irreführenden Simulationsanzeige betrieben werden.
        if simulate and controller is not None and not isinstance(controller, SimulatedDaqController):
            raise ValueError("Simulationsmodus benötigt einen SimulatedDaqController.")
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")
        super().__init__()
        # Tk 9 stellt CTks Formen-Schrift je nach X11-System unvollständig dar.
        # Polygonformen vermeiden dort defekte Ecken; Tk 8.6 behält den Standard.
        if float(self.tk.call("info", "tclversion")) >= 9:
            ctk.DrawEngine.preferred_drawing_method = "polygon_shapes"
        self.simulate = simulate
        self.title("Nassheuer | Luftsteuerung" + (" | Simulation" if simulate else " | Hardware"))
        self.geometry("1180x860")
        self.minsize(940, 650)
        self.configure(fg_color="#F2F5F7")
        # Getrennte Zustände statt eines mehrdeutigen „läuft“-Flags:
        # closing: aktueller Abschlussversuch; shutdown_blocked: kein Wiederanlauf.
        self.closing = False
        self.shutdown_blocked = False
        self.confirming = False
        self.output_busy = False
        self.measurement_valid = False
        self.measurement_error = False
        self.sensor_readings = {name: SensorReading() for name in SENSOR_CHANNELS}
        self.last_measurement_at = 0.0
        self.last_currents = None
        self.measurement_message = "Initialisierung läuft · keine Messwerte"
        # Unbestätigter Startzustand; echte Ressourceninformationen kommen erst per Ereignis.
        self.daq_state = DaqState(False, (), False, False)
        self.poll_after_id = None
        self.shutdown_started_at = 0.0
        self.worker = DaqWorker(controller if controller is not None else (
            SimulatedDaqController() if simulate else DaqController()))
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build_gui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._log("Simulation ohne NI-Zugriff gestartet." if simulate else
                  "Hardwaremodus ausgewählt. Keine Ausgangswerte beim Start.")
        # Erst nach Widgetaufbau darf der Thread Ereignisse liefern; kein zweiter Tk-Loop.
        self.worker.start()
        # 50-ms-Tk-Poller und ca. 200-ms-Messpakete besitzen absichtlich getrennte Takte.
        self.poll_after_id = self.after(50, self.update_measurement)

    # Erstellt einheitlich formatierte Beschriftungen.
    def _label(self, parent, text, *, size=13, bold=False, color=COLOR_DARK, **kwargs):
        return ctk.CTkLabel(parent, text=text, text_color=color,
                            font=ctk.CTkFont(size=size, weight="bold" if bold else "normal"),
                            **kwargs)

    # Erstellt Kopfbereich, Reiter und den Schließen-Button.
    def _build_gui(self) -> None:
        header = ctk.CTkFrame(self, fg_color=COLOR_DARK, corner_radius=0)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        self._label(header, "NASSHEUER  /  LUFTSTEUERUNG", size=24, bold=True,
                    color="white").grid(row=0, column=0, sticky="w", padx=26, pady=(18, 0))
        self._label(header, "Manuelle Stellbefehle und kontinuierliche Durchflussanzeige",
                    color="#C4D5DE").grid(row=1, column=0, sticky="w", padx=26, pady=(0, 18))
        # Betriebsmodus bleibt unabhängig vom geöffneten Reiter sichtbar.
        mode = "SIMULATION · keine Hardware" if self.simulate else "HARDWARE · manueller Betrieb"
        self._label(header, mode, bold=True, color="#F5CE82").grid(
            row=0, column=1, sticky="e", padx=26, pady=(18, 0))
        self.connection_label = self._label(header, "INITIALISIERUNG", color="#C4D5DE")
        self.connection_label.grid(row=1, column=1, sticky="e", padx=26, pady=(0, 18))
        self.tabs = ctk.CTkTabview(self, fg_color="#F2F5F7", corner_radius=10,
                                   segmented_button_selected_color=COLOR_BLUE)
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=18, pady=(4, 0))
        self._build_control_tab(self.tabs.add("Steuerung"))
        self._build_settings_tab(self.tabs.add("Einstellungen"))
        self._build_regulation_tab(self.tabs.add("Regelung"))
        self.tabs.set("Steuerung")
        # Abschluss außerhalb des Scrollbereichs, damit er jederzeit erreichbar bleibt.
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=26, pady=(5, 12))
        footer.grid_columnconfigure(0, weight=1)
        self._label(footer, "Task schließen ≠ Anlage abschalten · Keine sicherheitsgerichtete Steuerung",
                    size=11, color=TEXT_MUTED).grid(row=0, column=0, sticky="w")
        self.close_button = ctk.CTkButton(footer, text="Programm schließen", width=170,
                                          fg_color=COLOR_DARK, command=self.on_close)
        self.close_button.grid(row=0, column=1, sticky="e")

    # Erstellt Eingabekarten, Messwertanzeige, Status und Ereignisprotokoll.
    def _build_control_tab(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        content = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        content.grid(row=0, column=0, sticky="nsew")
        content.grid_columnconfigure((0, 1, 2), weight=1, uniform="cards")
        self._label(content, "01   MANUELLE STELLBEFEHLE", size=15, bold=True).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=8, pady=(5, 2))
        self._label(content, "Sollwerte in % · Nur ausgefüllte, freigegebene Kanäle übernehmen",
                    size=12, color=TEXT_MUTED).grid(row=1, column=0, columnspan=3,
                                                  sticky="w", padx=8, pady=(0, 12))
        # Fachliche Namen verbinden Widgets; keine Annahme aus numerischer Widgetreihenfolge.
        self.inputs, self.command_labels = {}, {}
        # StringVars explizit diesem Fenster zuordnen und Referenzen behalten.
        # So funktionieren wiederholte Fensterstarts ohne Tk-Defaultroot-Lecks.
        self.entry_variables = {}
        for col, (name, subtitle) in enumerate((("FU", "Frequenzumrichter"),
                                               ("GLF", "Gleitschieber Low Flow"),
                                               ("GHF", "Gleitschieber High Flow"))):
            self._actuator_card(content, col, name, subtitle)
        actions = ctk.CTkFrame(content, fg_color=SUBTLE_BG, corner_radius=9)
        actions.grid(row=3, column=0, columnspan=3, sticky="ew", padx=6, pady=(8, 4))
        actions.grid_columnconfigure(0, weight=1)
        self.preview_label = self._label(actions, "FU-Stromvorschau: — mA", bold=True)
        self.preview_label.grid(row=0, column=0, sticky="w", padx=14, pady=(12, 0))
        self._label(actions, "Rechnung ohne Ausgabe", size=11, color=TEXT_MUTED).grid(
            row=1, column=0, sticky="w", padx=14, pady=(0, 12))
        self.clear_button = ctk.CTkButton(actions, text="Eingaben leeren", width=130,
                                          fg_color="#D7E3E9", text_color=COLOR_DARK,
                                          hover_color="#C7D7DF", command=self._clear_inputs)
        self.clear_button.grid(row=0, column=1, rowspan=2, padx=10)
        # Bis zum ersten aktuellen gültigen Messwert bewusst nicht bedienbar.
        self.output_button = ctk.CTkButton(actions,
            text="Simulation übernehmen" if self.simulate else "Stellbefehle senden …",
            width=200, height=36, fg_color=COLOR_BLUE, hover_color="#0D5669",
            state="disabled", command=self.output_update)
        self.output_button.grid(row=0, column=2, rowspan=2, padx=(0, 14))
        self.output_status = self._label(content, "Noch kein Stellbefehl gesendet", size=12,
                                          color=TEXT_MUTED, anchor="w", justify="left", wraplength=850)
        self.output_status.grid(row=4, column=0, columnspan=3, sticky="ew", padx=10, pady=(3, 15))
        self._label(content, "02   DURCHFLUSS · ISTWERTE", size=15, bold=True).grid(
            row=5, column=0, columnspan=3, sticky="w", padx=8, pady=(2, 10))
        measures = ctk.CTkFrame(content, fg_color="transparent")
        measures.grid(row=6, column=0, columnspan=3, sticky="ew")
        measures.grid_columnconfigure((0, 1), weight=1, uniform="sensors")
        # Zahl, Einheit/Strom und Qualität bleiben pro Messstelle getrennt aktualisierbar.
        self.value_labels, self.current_labels, self.quality_labels = {}, {}, {}
        self._measurement_card(measures, 0, "GHF", "High Flow", AI_GHF)
        self._measurement_card(measures, 1, "GLF", "Low Flow", AI_GLF)
        self.measurement_status = self._label(content, self.measurement_message, size=12,
                                                color=COLOR_ORANGE, anchor="w", wraplength=850)
        self.measurement_status.grid(row=7, column=0, columnspan=3, sticky="ew", padx=10, pady=(5, 12))
        system = ctk.CTkFrame(content, fg_color=SUBTLE_BG, corner_radius=9)
        system.grid(row=8, column=0, columnspan=3, sticky="ew", padx=6, pady=(0, 12))
        self.task_label = self._label(system, "Tasks: noch nicht initialisiert", size=12,
                                      anchor="w", wraplength=850, justify="left")
        self.task_label.pack(fill="x", padx=14, pady=(9, 2))
        self._label(system, "Kennlinien: FU 4–20 mA · GLF/GHF 0–20 mA · DAQ-Write ist keine Rückmessung",
                    size=11, color=TEXT_MUTED).pack(anchor="w", padx=14, pady=(0, 9))
        self._label(content, "03   EREIGNISPROTOKOLL", size=13, bold=True).grid(
            row=9, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 5))
        self.log_box = ctk.CTkTextbox(content, height=105, fg_color=CARD_BG,
                                      text_color=COLOR_DARK, font=ctk.CTkFont(size=12))
        self.log_box.grid(row=10, column=0, columnspan=3, sticky="ew", padx=6, pady=(0, 8))
        self.log_box.configure(state="disabled")

    # Erstellt das Eingabefeld und die Befehlsanzeige für einen Ausgang.
    def _actuator_card(self, parent, column: int, name: str, subtitle: str) -> None:
        card = ctk.CTkFrame(parent, fg_color=CARD_BG, corner_radius=10)
        card.grid(row=2, column=column, sticky="nsew", padx=6)
        card.grid_columnconfigure(0, weight=1)
        self._label(card, name, size=23, bold=True).grid(row=0, column=0, sticky="w", padx=16, pady=(13, 0))
        self._label(card, subtitle, size=12, color=TEXT_MUTED).grid(row=1, column=0, sticky="w", padx=16)
        # Kennlinienfreigabe beschreibt Konfiguration, keinen eingeschalteten Ausgang.
        available = name in CONFIGURED_ACTUATOR_CURVES
        self._label(card, "KENNLINIE VORHANDEN" if available else "AUSGABE GESPERRT", size=10,
                    bold=True, color=COLOR_BLUE if available else COLOR_ORANGE).grid(
            row=2, column=0, sticky="w", padx=16, pady=(5, 8))
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.grid(row=3, column=0, sticky="ew", padx=16)
        self._label(row, "Sollwert", size=12).pack(side="left", padx=(0, 8))
        var = ctk.StringVar(master=self, value="")
        self.entry_variables[name] = var
        entry = ctk.CTkEntry(row, width=90, height=34, textvariable=var,
                             placeholder_text="0–100", fg_color="#F7F9FB", text_color=COLOR_DARK)
        entry.pack(side="left")
        self._label(row, "%", color=TEXT_MUTED).pack(side="left", padx=6)
        if not available:
            entry.configure(state="disabled")
        self.inputs[name] = entry
        if name == "FU":
            self.fu_variable = var
            # Die Trace-Metadaten werden nicht benötigt, gelesen wird nur der aktuelle Text.
            var.trace_add("write", lambda *_: self._update_preview())
        self._label(card, "Mod" + AO_CHANNELS[name].split("Mod")[-1],
                    size=10, color=TEXT_MUTED).grid(row=4, column=0, sticky="w", padx=16, pady=(7, 0))
        label = self._label(card, "Letzter Befehl: —", size=11, color=TEXT_MUTED,
                             justify="left", anchor="w", wraplength=245)
        label.grid(row=5, column=0, sticky="ew", padx=16, pady=(3, 13))
        # Bestätigung bleibt sichtbar, wenn das dazugehörige Eingabefeld später leer ist.
        self.command_labels[name] = label

    # Erstellt die Anzeige für Durchfluss, Sensorstrom und Messqualität.
    def _measurement_card(self, parent, column, name, title, channel) -> None:
        card = ctk.CTkFrame(parent, fg_color=CARD_BG, corner_radius=10)
        card.grid(row=0, column=column, sticky="nsew", padx=6)
        card.grid_columnconfigure(0, weight=1)
        self._label(card, f"{name} / {title}", size=13, bold=True).grid(
            row=0, column=0, sticky="w", padx=18, pady=(12, 0))
        value = self._label(card, "—", size=35, bold=True)
        value.grid(row=1, column=0, sticky="w", padx=18, pady=0)
        self._label(card, "m³/h", size=16, color=TEXT_MUTED).grid(row=1, column=1, sticky="e", padx=18)
        current = self._label(card, "Sensorstrom: — mA", size=12, color=TEXT_MUTED)
        current.grid(row=2, column=0, sticky="w", padx=18)
        quality = self._label(card, "Warte auf Messdaten", size=11, color=COLOR_ORANGE)
        quality.configure(wraplength=330, justify="left", anchor="w")
        quality.grid(row=3, column=0, columnspan=2, sticky="w", padx=18, pady=(2, 10))
        self.value_labels[name], self.current_labels[name], self.quality_labels[name] = value, current, quality

    # Zeigt die konfigurierten Kanalnamen und Grenzwerte an. Keine Hardwareprüfung.
    def _build_settings_tab(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        card = ctk.CTkScrollableFrame(tab, fg_color=CARD_BG)
        card.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self._label(card, "Hardware-Zuordnung", size=22, bold=True).pack(anchor="w", padx=20, pady=(20, 4))
        self._label(card, "Aus beiden Quelldateien übernommen · physische Zuordnung noch zu prüfen",
                    color=COLOR_ORANGE).pack(anchor="w", padx=20, pady=(0, 15))
        for name, channel in [*AO_CHANNELS.items(), ("GHF-Sensor", AI_GHF), ("GLF-Sensor", AI_GLF)]:
            row = ctk.CTkFrame(card, fg_color=SUBTLE_BG, corner_radius=5)
            row.pack(fill="x", padx=20, pady=3)
            self._label(row, name, bold=True, width=130, anchor="w").pack(side="left", padx=12, pady=7)
            self._label(row, channel, color=TEXT_MUTED).pack(side="left", padx=10)
        self._label(card,
            "Messung: NI-9253 · ±20 mA konfiguriert · 1.000 Samples/s je Kanal\n"
            "Ausgabe: NI-9266 · 0–20 mA konfiguriert · FU-Skalierung 4–20 mA\n"
            "Sensor-Nennbereich: 4–20 mA · akzeptierte Messtoleranz: 3,9–20,1 mA\n"
            "Anzeige alle 200 ms · veraltet nach 1 s\n\n"
            "GLF/GHF-Stellglieder: bestätigt linear 0–100 % = 0–20 mA.\n"
            "Sensor-Durchflussformeln bleiben davon vollständig getrennt.\n\n"
            "Kein automatischer Wiederanlauf, kein Fehler-Reset, keine Abschaltstrom-Ausgabe.",
            justify="left", anchor="w", wraplength=780, color=TEXT_MUTED).pack(
                anchor="w", padx=20, pady=20)

    # Zeigt an, dass noch kein automatischer Regler eingebaut ist.
    def _build_regulation_tab(self, tab) -> None:
        card = ctk.CTkFrame(tab, fg_color=CARD_BG)
        card.pack(fill="both", expand=True, padx=8, pady=8)
        self._label(card, "Regelung", size=22, bold=True).pack(anchor="w", padx=24, pady=(24, 8))
        self._label(card, "Nicht implementiert", bold=True, color=COLOR_ORANGE).pack(anchor="w", padx=24)
        self._label(card, "Die Quelldateien enthalten keinen automatischen Regler.\n"
                    "Diese Version unterstützt manuelle Stellbefehle und die Durchflussanzeige.\n"
                    "Simulierte Sensorwerte sind kein Modell der realen Luftstrecke.",
                    justify="left", color=TEXT_MUTED, wraplength=760).pack(anchor="w", padx=24, pady=14)

    # Schreibt Meldungen mit Uhrzeit in das sichtbare Ereignisprotokoll.
    def _log(self, text: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{datetime.now():%H:%M:%S}] {text}\n")
        # Begrenztes Protokoll; kein unbegrenztes Wachstum im Dauerbetrieb.
        if int(self.log_box.index("end-1c").split(".")[0]) > 300:
            self.log_box.delete("1.0", "2.0")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    # Leert Eingabefelder. Bereits ausgegebene Sollströme bleiben unverändert.
    def _clear_inputs(self) -> None:
        if self.output_busy or self.confirming or self.closing:
            return
        for name, entry in self.inputs.items():
            if name in CONFIGURED_ACTUATOR_CURVES:
                entry.delete(0, "end")
        self._update_preview()
        self.output_status.configure(text="Eingaben geleert · Ausgänge unverändert", text_color=TEXT_MUTED)

    # Zeigt den berechneten FU-Strom, ohne eine Ausgabe auszulösen.
    def _update_preview(self, _event=None) -> None:
        # Traces können vor der vollständigen Widget-Erstellung feuern.
        if not hasattr(self, "preview_label"):
            return
        try:
            percent = parse_percent(self.inputs["FU"].get(), "FU")
            text = "— mA" if percent is None else number_de(fu_percent_to_amp(percent) * 1000, 3) + " mA"
        except ValueError:
            text = "Ungültige Eingabe"
        self.preview_label.configure(text="FU-Stromvorschau: " + text)

    # Prüft im GUI-Thread, ob der letzte gültige Messwert noch aktuell ist.
    def _measurement_is_recent(self) -> bool:
        now = time.monotonic()
        # Nur die Ausgabefreigabe bleibt gemeinsam. Eine teilweise gültige
        # Messung wird angezeigt, ersetzt aber nicht die bisherige Vorbedingung.
        return (not self.measurement_error and all(
            reading.valid and reading.recent(now) for reading in self.sensor_readings.values()))

    # Aktiviert oder sperrt Eingaben und Buttons passend zum aktuellen Status.
    def _refresh_controls(self) -> None:
        # Gemeinsame Freigabebasis verhindert widersprüchliche Einzelbutton-Zustände.
        editing = not (self.closing or self.shutdown_blocked or self.confirming or self.output_busy)
        ready = editing and not self.daq_state.output_fault and self._measurement_is_recent()
        self.output_button.configure(state="normal" if ready else "disabled")
        self.clear_button.configure(state="normal" if editing else "disabled")
        for name, entry in self.inputs.items():
            entry.configure(state="normal" if editing and name in CONFIGURED_ACTUATOR_CURVES else "disabled")
        self.close_button.configure(state="disabled" if self.confirming or self.closing else "normal")

    # Aktualisiert Messwerte und kennzeichnet ungültige oder veraltete Daten.
    def _render_measurement(self) -> None:
        now = time.monotonic()
        summaries = []
        valid_count = 0
        # Jede Messkarte bewertet ihren eigenen Strom und Zeitstempel. Ein
        # Bereichsfehler bei GLF darf GHF weder löschen noch rot einfärben.
        for name, convert in (("GHF", ghf_amp_to_m3h), ("GLF", glf_amp_to_m3h)):
            reading = self.sensor_readings[name]
            value_text, raw_text = "—", "—"
            color = COLOR_ORANGE
            if self.closing or self.shutdown_blocked:
                quality, summary = "Abschluss aktiv · Messung ungültig", "Abschluss"
            elif self.measurement_error:
                quality, summary, color = "Messfehler · Erfassung beendet", "Messfehler", COLOR_RED
            elif reading.received_at and not reading.recent(now):
                quality, summary = "Veraltet · keine aktuellen Messdaten", "veraltet"
            elif not reading.received_at:
                quality, summary = reading.problem or "Warte auf Messdaten", "wartet"
            elif reading.current_a is None:
                quality, summary = reading.problem or "Messwert fehlt", "fehlt"
            else:
                # Endliche Rohströme einschließlich 0,000 mA unverändert zeigen.
                # NaN/Inf ausdrücklich als ungültig kennzeichnen, niemals als 0.
                if math.isfinite(reading.current_a):
                    # Bei einem Bereichsfehler mehr Stellen zeigen: 3,8999 mA
                    # darf nicht auf scheinbar zulässige 3,900 mA gerundet werden.
                    raw_text = number_de(reading.current_a * 1000, 3 if reading.valid else 6)
                else:
                    raw_text = ("NaN" if math.isnan(reading.current_a) else
                                "+∞" if reading.current_a > 0 else "−∞")
                if reading.valid:
                    value_text = number_de(convert(reading.current_a))
                    quality = "Simulierter Wert" if self.simulate else "Gültiges Stromsignal"
                    quality += f" · Alter {number_de(now - reading.received_at, 1)} s"
                    summary = "gültig"
                    color = COLOR_BLUE if self.simulate else COLOR_GREEN
                    valid_count += 1
                else:
                    # Durchfluss außerhalb der Sensor-Kennlinie nicht als valide
                    # physikalische Messung ausgeben. Rohstrom bleibt sichtbar.
                    quality = "Fehler: " + (reading.problem or "Ungültiger Sensorstrom")
                    summary, color = "Fehler", COLOR_RED
            self.value_labels[name].configure(text=value_text)
            self.current_labels[name].configure(text="Sensorstrom: " + raw_text + " mA",
                                                 text_color=color if summary == "Fehler" else TEXT_MUTED)
            self.quality_labels[name].configure(text=quality, text_color=color)
            summaries.append(f"{name}: {summary}")
        summary_text = " · ".join(summaries)
        summary_color = (COLOR_BLUE if self.simulate else COLOR_GREEN) if valid_count == 2 else COLOR_ORANGE
        if self.measurement_error:
            summary_color = COLOR_RED
        self.measurement_status.configure(text=summary_text, text_color=summary_color)
        self.connection_label.configure(
            text=("SIMULIERT · " if self.simulate else "") + summary_text.upper(),
            text_color="#B8DEEB" if valid_count == 2 else "#F5CE82")
        # Existierende Tasks bestätigen nur Ressourcenbesitz, nicht elektrische Ausgangswerte.
        outputs = ", ".join(self.daq_state.output_tasks) or "keine"
        self.task_label.configure(text=("Simulierte Tasks" if self.simulate else "DAQ-Tasks") +
            f": Mess-Task {'vorhanden' if self.daq_state.measurement_task else 'nicht vorhanden'}"
            f" | Ausgabe-Tasks: {outputs} | Ausgabefehler: {'JA · gesperrt' if self.daq_state.output_fault else 'keiner gemeldet'}")
        self._refresh_controls()

    # Zeigt nur bestätigte Befehle und leert deren Eingabefelder.
    def _show_written(self, commands) -> None:
        for name, percent, current in commands:
            self.command_labels[name].configure(text=
                f"{'Simulierter Befehl' if self.simulate else 'DAQ-Write bestätigt'}:\n"
                f"{number_de(percent)} % / {number_de(current * 1000, 3)} mA\n"
                "Kein gemessener Ausgangsstrom", text_color=COLOR_BLUE)
            # Eingaben sind während des Auftrags gesperrt; nur erfolgreiche leeren.
            self.inputs[name].configure(state="normal")
            self.inputs[name].delete(0, "end")
        self._update_preview()

    # Liest Worker-Meldungen und plant mit after() den nächsten GUI-Durchlauf.
    def update_measurement(self) -> None:
        """Einziger GUI-Poller: Ergebnisse abholen, Frische prüfen, erneut planen."""
        self.poll_after_id = None
        # Queue-Rückstau darf die nächste Bedienaktion nicht unbegrenzt verzögern.
        for _ in range(100):
            try:
                kind, payload = self.worker.events.get_nowait()
            except queue.Empty:
                break
            if kind == "state":
                self.daq_state = payload
            # Originalen Worker-Zeitstempel übernehmen statt durch „jetzt“ zu ersetzen.
            elif kind == "measurement":
                readings, self.last_measurement_at = payload
                # Zustände enthalten unveränderliche Einzelwerte; keine Widgets
                # oder lebenden Task-Referenzen kommen aus dem Worker-Thread.
                previous = self.sensor_readings
                self.sensor_readings = readings
                self.last_currents = tuple(readings[name].current_a for name in SENSOR_CHANNELS)
                self.measurement_valid = all(reading.valid for reading in readings.values())
                self.measurement_error = False
                # Nur einen geänderten Fehlerstatus protokollieren, nicht jeden
                # identischen 200-ms-Messzyklus. Der betroffene Kanal ist benannt.
                for name, reading in readings.items():
                    if reading.problem and reading.problem != previous[name].problem:
                        raw = ("fehlt" if reading.current_a is None else
                               number_de(reading.current_a * 1000, 6) + " mA")
                        self._log(f"{name} / {SENSOR_CHANNELS[name]}: {raw} · {reading.problem}")
            elif kind == "measurement_error":
                self.measurement_valid = False
                self.measurement_error = True
                self.sensor_readings = {name: SensorReading(problem="DAQ-Messfehler")
                                        for name in SENSOR_CHANNELS}
                self.measurement_message = str(payload)
                self._log(str(payload))
            elif kind == "started":
                self._log("Mess-Task gestartet: GHF ai0 / GLF ai1, 1.000 Hz" +
                          (" (simuliert)." if self.simulate else "."))
            # Erst dieses Ereignis berechtigt zu einer sichtbaren Erfolgsbestätigung.
            elif kind == "write_ok":
                self.output_busy = False
                self._show_written(payload)
                self.output_status.configure(text=("Simulierter Befehl verarbeitet" if self.simulate
                    else "DAQ-Write bestätigt · physikalischer Ausgangsstrom nicht gemessen"), text_color=COLOR_BLUE)
                self._log(("Simulierter Befehl: " if self.simulate else "DAQ-Write: ") +
                          ", ".join(f"{n} {number_de(p)} % / {number_de(a * 1000, 3)} mA" for n, p, a in payload))
            # Teilbestätigung ehrlich darstellen, kein Gesamt-Erfolg und kein Rollback.
            elif kind == "write_error":
                self.output_busy = False
                detail, successful = payload
                self._show_written(successful)
                self.output_status.configure(text="Ausgabefehler · gesperrt · Ausgangszustand unbekannt",
                                             text_color=COLOR_RED)
                self._log("Ausgabefehler: " + detail + " | Bestätigt: " +
                          (", ".join(n for n, _, _ in successful) or "keine"))
            # Nur ohne gemeldete Schließfehler das Fenster endgültig zerstören.
            elif kind == "closed":
                if not payload:
                    # Genau ein Tk-Interpreter gehört dieser Anwendung. Auch noch
                    # wartende CTk-interne Zeichen-/Skalierungs-Callbacks abbrechen,
                    for callback_id in self.tk.call("after", "info"):
                        # Nur die Planung entfernen. Das jeweilige CTk-Kindwidget
                        # löscht sein registriertes Python/Tcl-Kommando beim destroy.
                        self.tk.call("after", "cancel", callback_id)
                    self.destroy()
                    return
                self.closing = False
                self.output_busy = False
                self.output_status.configure(text="Schließfehler · nur erneutes Schließen zulässig", text_color=COLOR_RED)
                self._log("Schließfehler: " + "; ".join(payload))
                self.close_button.configure(text="Schließen erneut versuchen")
        # Längeres Warten erklären, aber keinen noch aktiven Treiberaufruf gewaltsam beenden.
        if self.closing and time.monotonic() - self.shutdown_started_at > 6:
            self.output_status.configure(text="Warte auf DAQ-Treiber / Ressourcenfreigabe · noch nicht geschlossen",
                                         text_color=COLOR_ORANGE)
        # Auch ohne neue Nachricht müssen Messalter und Bedienbarkeit weiter geprüft werden.
        self._render_measurement()
        # 50-ms-Tk-Poller und ca. 200-ms-Messpakete besitzen absichtlich getrennte Takte.
        self.poll_after_id = self.after(50, self.update_measurement)

    # Prüft die Sollwerte, fragt nach Bestätigung und übergibt sie dem Worker.
    def output_update(self) -> None:
        if self.confirming or self.output_busy or self.closing or self.shutdown_blocked or self.daq_state.output_fault:
            return
        try:
            commands = prepare_commands({name: entry.get() for name, entry in self.inputs.items()})
            if not commands:
                self.output_status.configure(text="Keine Sollwerte eingegeben", text_color=TEXT_MUTED)
                return
            if not self._measurement_is_recent():
                raise RuntimeError("Keine aktuelle gültige Messung · Ausgabe gesperrt")
        except (ValueError, RuntimeError) as exc:
            self.output_status.configure(text=str(exc), text_color=COLOR_RED)
            self._log("Sollwertübernahme verweigert: " + str(exc))
            return
        self.confirming = True  # Vor dem modalen Dialog: verschachtelte Callbacks sperren.
        self._refresh_controls()
        try:
            details = "\n".join(f"{n}: {number_de(p)} % → {number_de(a * 1000, 3)} mA" for n, p, a in commands)
            confirmed = messagebox.askyesno(
                "Simulierten Befehl bestätigen" if self.simulate else "Reale Stromausgabe bestätigen",
                ("Nur Simulation, keine realen Ausgänge.\n\n" if self.simulate else
                 "Stellbefehle an NI-9266 senden? Nur bei freigegebener Anlage\n"
                 "und unabhängiger Abschaltmöglichkeit.\n\n") + details +
                "\n\nDAQ-Bestätigung ist keine Rückmessung des Ausgangsstroms.", parent=self)
        finally:
            self.confirming = False
        if not confirmed:
            self.output_status.configure(text="Befehl abgebrochen · keine Ausgabe", text_color=TEXT_MUTED)
        # Ein vor dem Dialog gültiger Zustand kann während der Bestätigung ungültig werden.
        elif self.closing or self.shutdown_blocked or self.daq_state.output_fault or not self._measurement_is_recent():
            self.output_status.configure(text="Abbruch: Messung während Bestätigung ungültig", text_color=COLOR_RED)
        else:
            # Auftragsannahme ist zunächst nur Queue-Bestätigung, kein Hardware-Erfolg.
            self.output_busy = self.worker.request_write(commands)
            self.output_status.configure(text="Befehl angefordert · noch nicht bestätigt" if self.output_busy
                                         else "Befehl nicht angenommen", text_color=COLOR_ORANGE)
        self._refresh_controls()

    # Fragt bei Bedarf nach der Anlagensicherung und fordert die Task-Freigabe an.
    def on_close(self) -> None:
        if self.closing or self.confirming:
            return
        self.confirming = True
        self._refresh_controls()
        try:
            # Ausstehender Write kann vom Worker bereits übernommen worden sein.
            if not self.simulate and (self.daq_state.output_attempted or self.output_busy):
                secured = messagebox.askyesno("Anlage unabhängig gesichert?",
                    "Ausgänge können den letzten Stromwert halten oder ein Write ist noch aktiv.\n\n"
                    "Ist die Anlage über ihre unabhängige Einrichtung gesichert?\n"
                    "Task-Schließen ist keine Sicherheitsabschaltung.", parent=self)
                if not secured:
                    return
        finally:
            self.confirming = False
            self._refresh_controls()
        # Ab jetzt keine Wiederaufnahme, auch nicht nach einem Schließfehler.
        self.closing = True
        self.shutdown_blocked = True
        self.shutdown_started_at = time.monotonic()
        # Ressourcen ausschließlich durch ihren bisherigen Besitzerthread freigeben lassen.
        self.worker.request_close()
        self.output_status.configure(text="Ressourcen werden freigegeben · kein Abschaltstrom", text_color=COLOR_ORANGE)
        self._refresh_controls()


# Startet standardmäßig die Simulation. Echte Hardware erfordert --hardware.
def main() -> None:
    """Import ohne Fenster/Tasks; Hardwarebetrieb nur mit explizitem Argument."""
    parser = argparse.ArgumentParser(description="Nassheuer-Luftsteuerung (Standard: Simulation ohne NI-Zugriff)")
    # Gleichzeitiges --simulate/--hardware wird als widersprüchlich abgelehnt.
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--simulate", action="store_true", help="Simulation ohne NI-Hardware (Standard)")
    group.add_argument("--hardware", action="store_true", help="Reale Messung; Ausgaben erst nach Bestätigung")
    args = parser.parse_args()
    # Kein automatischer Hardware-Fallback. --simulate benennt ausdrücklich den Standard.
    app = LuftsteuerungApp(simulate=not args.hardware)
    app.mainloop()


# Tests importieren dieses Modul, ohne GUI oder Tasks zu starten.
if __name__ == "__main__":
    main()

