"""NI-DAQ-Zugriffe und die bereits vorhandene Simulation.

DaqController besitzt die Treiber-Tasks, also die geöffneten Mess- und
Ausgabeaufträge. SimulatedDaqController verwendet dieselbe Schnittstelle mit
reinen Softwaremarkern und Beispieldaten. Beide bleiben bewusst zusammen,
damit die bestehende Vererbung und die Aufgabenverteilung unverändert bleiben.

Nur die Methoden des echten Controllers laden nidaqmx bei Bedarf. Das bloße
Importieren dieser Datei und der Simulationsbetrieb benötigen keinen NI-Treiber.
Im laufenden Programm ruft der Worker diese Methoden auf, nicht der GUI-Thread.
Wichtig: Der originale Hardware-Abschluss fordert 4 mA an FU, GLF und GHF an.
"""

from __future__ import annotations

import math
import time

from .konfiguration import (
    AI_GHF,
    AI_GLF,
    AO_CHANNELS,
    MAX_OUTPUT_A,
    MIN_OUTPUT_A,
    SAMPLE_RATE_HZ,
)
from .kennlinien import CONFIGURED_ACTUATOR_CURVES, validate_current


# Bündelt echte NI-DAQ-Aufrufe und verwaltet Mess- und Ausgangs-Tasks.
class DaqController:
    """Besitzt den Mess-Task und die unabhängigen Ausgangs-Tasks.

    __init__ erstellt noch keine NI-DAQ-Tasks. Das übernommene close_tasks()
    fordert dagegen an allen drei Ausgängen 4 mA an, bevor es die Tasks
    freigibt. Das ist das vorhandene Verhalten, keine neue Abschaltlogik.
    Ein erfolgreicher Schreibaufruf ist keine Messung des Ausgangsstroms.
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
        # Zusammenfassender Fehlerstatus. write_current() verwaltet Fehler pro
        # Ausgang; ein neuer manueller Auftrag kann einen erneuten Versuch auslösen.
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
                AI_GHF, min_val=0.0035, max_val=0.020
            )
            task.ai_channels.add_ai_current_chan(
                AI_GLF, min_val=0.0035, max_val=0.020
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
        """Manueller Schreibversuch; frühere Fehler sperren nicht alle Kanäle."""
        if name not in AO_CHANNELS:
            raise ValueError(f"Unbekannter Ausgang: {name}")
        if name not in CONFIGURED_ACTUATOR_CURVES:
            raise RuntimeError(f"{name}: Keine bestätigte Stellgliedkennlinie.")
        current_a = validate_current(current_a)

        # Fehler pro Ausgang merken, statt die gesamte Bedienung zu verriegeln.
        if not hasattr(self, "_failed_outputs"):
            self._failed_outputs = set()

        # Nur ein neuer Bedienauftrag löst einen erneuten Versuch aus.
        # Einen unvollständig konfigurierten Task nicht wiederverwenden.
        if name in self._failed_outputs:
            previous_task = self.output_tasks.get(name)
            if previous_task is not None:
                try:
                    previous_task.close()
                except Exception as exc:
                    self.output_fault = True
                    raise RuntimeError(
                        f"{name} / {AO_CHANNELS[name]}: Vorherigen Ausgangs-Task "
                        f"nicht freigegeben; kein neuer Task angelegt. "
                        f"Der neue Sollstrom wurde nicht geschrieben. {exc}"
                    ) from exc
                else:
                    self.output_tasks.pop(name, None)

        self.output_attempted = True
        task = self.output_tasks.get(name)
        newly_created = task is None
        operation = "Strom schreiben"

        try:
            if newly_created:
                operation = "Ausgangs-Task anlegen"
                if self.driver is None:
                    import nidaqmx
                    self.driver = nidaqmx

                task = self.driver.Task()
                self.output_tasks[name] = task

                operation = "Stromkanal konfigurieren"
                task.ao_channels.add_ao_current_chan(
                    AO_CHANNELS[name],
                    min_val=MIN_OUTPUT_A,
                    max_val=MAX_OUTPUT_A,
                )

            operation = "Strom schreiben"
            count = task.write(current_a, auto_start=True, timeout=5.0)
            if count != 1:
                raise RuntimeError("DAQ bestätigte nicht genau ein Sample.")

            self.last_written[name] = current_a
            self._failed_outputs.discard(name)
            self.output_fault = bool(self._failed_outputs)

        except Exception as exc:
            self._failed_outputs.add(name)
            self.output_fault = True
            # Referenz nur nach bestätigtem close() entfernen.
            if newly_created and task is not None:
                try:
                    task.close()
                except Exception:
                    pass
                else:
                    self.output_tasks.pop(name, None)

            raise RuntimeError(
                f"{name} / {AO_CHANNELS[name]}: {operation} für "
                f"{current_a * 1000:.3f} mA fehlgeschlagen. {exc}"
            ) from exc

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

    # Schreibt vor dem Freigeben der Tasks alle drei Ausgänge auf 4 mA.
    def close_tasks(self) -> list[str]:
        """FU 0 %, GLF/GHF 100 % anfordern und danach die Tasks freigeben."""
        problems: list[str] = []
        shutdown_current_a = 0.004  # 4 mA in Ampere

        # Auch bisher unbenutzte Ausgänge müssen ihren Abschlusswert erhalten.
        for name in ("FU", "GLF", "GHF"):
            task = self.output_tasks.get(name)
            newly_created = task is None
            self.output_attempted = True

            try:
                validate_current(shutdown_current_a)

                if newly_created:
                    # Deckt auch Schließen vor dem Start der Messung ab.
                    if self.driver is None:
                        import nidaqmx
                        self.driver = nidaqmx

                    task = self.driver.Task()
                    self.output_tasks[name] = task
                    task.ao_channels.add_ao_current_chan(
                        AO_CHANNELS[name],
                        min_val=MIN_OUTPUT_A,
                        max_val=MAX_OUTPUT_A,
                    )

                # Fest vorgegebener Endwert, keine normale Sollwertübernahme.
                # Eine bestehende Fehlersperre wird dabei NICHT zurückgesetzt.
                count = task.write(
                    shutdown_current_a, auto_start=True, timeout=5.0
                )
                if count != 1:
                    raise RuntimeError("DAQ bestätigte nicht genau ein Sample.")

                self.last_written[name] = shutdown_current_a

            except Exception as exc:
                self.output_fault = True
                problems.append(f"{name}: 4-mA-Befehl fehlgeschlagen: {exc}")

                # Einen neu angelegten, fehlgeschlagenen Task sauber entfernen.
                if newly_created and task is not None:
                    try:
                        task.close()
                    except Exception as close_exc:
                        problems.append(f"{name}: Task-Freigabe fehlgeschlagen: {close_exc}")
                    else:
                        self.output_tasks.pop(name, None)

        # Bei einem Fehler bleibt das Fenster für einen erneuten Versuch offen.
        # Bereits vorhandene Tasks behalten wir bis zum nächsten Schließversuch.
        if problems:
            return problems

        # Erst nach drei bestätigten Schreibaufrufen die Ausgabetasks freigeben.
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
        current_a = validate_current(current_a)
        self.output_attempted = True
        self.output_tasks.setdefault(name, object())
        self.last_written[name] = current_a
        self.output_fault = False

    # Entfernt den Marker der simulierten Messung.
    def close_measurement(self) -> list[str]:
        self.measurement_task = None
        return []

    # Entfernt alle simulierten Task-Marker.
    def close_tasks(self) -> list[str]:
        self.output_tasks.clear()
        self.measurement_task = None
        return []
