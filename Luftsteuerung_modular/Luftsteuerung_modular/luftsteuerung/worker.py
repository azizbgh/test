"""Hintergrundthread für Messung, Stellaufträge und Ressourcenfreigabe.

Die Oberfläche muss auf Klicks reagieren können, während der NI-Treiber wartet.
Dafür übernimmt DaqWorker die Treiberaufrufe in einem eigenen Thread. Zwei
Queues funktionieren wie getrennte Postfächer: commands enthält Aufträge der
Oberfläche, events enthält Antworten des Workers. Widgets werden nicht übergeben.

Der ursprüngliche Ablauf einschließlich Sperren, Zeitgrenzen, Teilbestätigungen,
Messneustart, Startwerten und Abschlussversuchen bleibt erhalten. run() fordert
beim Start für FU, GLF und GHF jeweils 4 mA an; der reale Controller fordert
beim Abschluss ebenfalls 4 mA an. Die Simulation verwendet keine NI-Ausgänge.
"""

from __future__ import annotations

import queue
import threading
import time

from .konfiguration import (
    AI_GHF,
    AI_GLF,
    GUI_REFRESH_MS,
    MAX_MEASUREMENT_AGE_S,
    SENSOR_CHANNELS,
)
from .kennlinien import (
    CONFIGURED_ACTUATOR_CURVES,
    validate_current,
    validate_percent,
)
from .hardware import DaqController
from .messwerte import DaqState, SensorReading, evaluate_sensor_currents


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
        # Befehle Tk -> Worker: „write“, „restart_measurement“ oder „close“.
        # Keine Widgets in Nutzdaten ablegen.
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

            # Alle Angaben vor dem ersten Hardwarezugriff überprüfen.
            for name, percent, current in commands:
                if name not in CONFIGURED_ACTUATOR_CURVES:
                    raise RuntimeError(f"{name}: Stellgliedkennlinie fehlt.")
                expected = validate_current(CONFIGURED_ACTUATOR_CURVES[name](
                    validate_percent(percent, name)))
                if validate_current(current) != expected:
                    raise ValueError(f"{name}: Strom passt nicht zur Kennlinie.")

            for name, percent, current in commands:
                if self.close_requested.is_set() or self.shutdown_blocked:
                    raise RuntimeError("Abschluss aktiv; Auftrag abgebrochen.")

                # Dieser Pfad dient bestätigten manuellen Aufträgen.
                # Fehlende Sensorfreigabe verhindert die Korrektur nicht.
                self.controller.write_current(name, current)
                successful.append((name, percent, current))

            self.events.put(("write_ok", tuple(successful)))

        except Exception as exc:
            # Eingabe-/Abbruchfehler sind keine neuen Hardwarefehler.
            # Echte Ausgangsfehler merkt der Controller pro Kanal.
            self.events.put(("write_error", (str(exc), tuple(successful))))

        finally:
            with self.accepted_lock:
                self.write_pending = False
            self.events.put(("state", self.state()))

    def request_restart_measurement(self) -> bool:
        """Ein Messneustart wird wie ein Schreibauftrag serialisiert."""
        with self.accepted_lock:
            if self.close_requested.is_set() or self.write_pending:
                return False
            self.write_pending = True
        self.commands.put(("restart_measurement", ()))
        return True

    def _restart_measurement(self) -> None:
        """Nur die Messung neu verbinden; keine Ausgangswerte schreiben."""
        self.measurement_running = False
        self.measurement_valid = False
        self.last_valid_at = 0.0
        self.sensor_readings = {
            name: SensorReading() for name in SENSOR_CHANNELS
        }
        self.events.put(("measurement_restarting", None))

        try:
            if self.close_requested.is_set() or self.shutdown_blocked:
                raise RuntimeError("Abschluss aktiv; kein Messneustart.")

            problems = self.controller.close_measurement()
            if problems:
                raise RuntimeError(
                    "Alter Mess-Task nicht freigegeben: " + "; ".join(problems)
                )

            if self.close_requested.is_set():
                raise RuntimeError("Abschluss angefordert; kein Messneustart.")

            self.controller.start_measurement()
            self.measurement_running = True
            self.events.put(("started", None))

        except Exception as exc:
            self.events.put((
                "measurement_error",
                f"Messneustart für GHF ({AI_GHF}) und GLF ({AI_GLF}) "
                f"fehlgeschlagen: {exc}",
            ))

        finally:
            # Auch ein fehlgeschlagener Neustart muss die Bedienung freigeben.
            with self.accepted_lock:
                self.write_pending = False
            self.events.put(("state", self.state()))
            self.events.put(("measurement_restart_done", None))

    # Initialisiert die Ausgänge, startet die Messung und bearbeitet danach Aufträge.
    def run(self) -> None:
        if not self.close_requested.is_set():
            successful = []

            # Feste Startwerte: FU 0 %, GLF/GHF 100 %, jeweils 4 mA.
            # Direkt über den Controller schreiben: Vor der Messung gibt es
            # noch keine gültigen Sensorwerte für die normale Sollwertübernahme.
            try:
                for name, percent in (
                    ("FU", 0.0),
                    ("GLF", 100.0),
                    ("GHF", 100.0),
                ):
                    # Eine inzwischen angeforderte Schließung hat Vorrang.
                    if self.close_requested.is_set():
                        break

                    self.controller.write_current(name, 0.004)
                    successful.append((name, percent, 0.004))

                # Erst nach drei bestätigten Writes vollständigen Erfolg melden.
                if len(successful) == 3:
                    self.events.put(("write_ok", tuple(successful)))

            except Exception as exc:
                # Bei einem Fehler keine weiteren Startwerte schreiben.
                # Teilerfolge anzeigen. Die vorhandene manuelle Bedienung
                # wird dadurch nicht dauerhaft gesperrt.
                self.controller.output_fault = True
                self.events.put((
                    "write_error",
                    (
                        f"Startinitialisierung auf 4 mA fehlgeschlagen: {exc}",
                        tuple(successful),
                    ),
                ))

            # Messung auch nach einem Ausgabefehler zur Diagnose starten.
            if not self.close_requested.is_set():
                try:
                    self.controller.start_measurement()
                    self.measurement_running = True
                    self.events.put(("started", None))
                except Exception as exc:
                    self.events.put((
                        "measurement_error",
                        f"Messinitialisierung fehlgeschlagen: {exc}",
                    ))

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
            elif action == "restart_measurement":
                self._restart_measurement()
                next_read = time.monotonic()
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
