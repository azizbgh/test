"""Vorhandene Basisoberfläche und gemeinsame Bedienabläufe.

LuftsteuerungApp hält den GUI-Zustand, baut ihre ursprüngliche Ansicht auf und
verarbeitet Eingaben, Bestätigungen, Worker-Ereignisse, Messwertanzeige und das
Schließen. Die Dashboardklasse erbt diese Abläufe und ersetzt gezielt einzelne
Darstellungsmethoden. Die ursprüngliche Basisansicht bleibt ebenfalls erhalten.

Tk-Widgets werden ausschließlich im GUI-Hauptthread bedient. Hardwareaufträge
gehen an den Worker. Die bestehenden Methoden und ihre Namen bleiben erhalten.
__file__ im Ereignisprotokoll bezeichnet nach der Verschiebung diese basis.py;
die Protokollanweisung selbst ist unverändert.
"""

from __future__ import annotations

import math
import queue
import time
from datetime import datetime
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

from ..konfiguration import (
    AI_GHF,
    AI_GLF,
    AO_CHANNELS,
    GUI_REFRESH_MS,
    MAX_MEASUREMENT_AGE_S,
    MAX_OUTPUT_A,
    MIN_OUTPUT_A,
    SAMPLE_RATE_HZ,
    SENSOR_CHANNELS,
    SENSOR_VALID_MAX_A,
    SENSOR_VALID_MIN_A,
    SENSOR_ZERO_A,
)
from ..kennlinien import (
    CONFIGURED_ACTUATOR_CURVES,
    fu_percent_to_amp,
    ghf_amp_to_m3h,
    glf_amp_to_m3h,
    parse_percent,
    prepare_commands,
)
from ..hardware import DaqController, SimulatedDaqController
from ..messwerte import DaqState, SensorReading
from ..worker import DaqWorker
from .darstellung import (
    CARD_BG,
    COLOR_BLUE,
    COLOR_DARK,
    COLOR_GREEN,
    COLOR_ORANGE,
    COLOR_RED,
    SUBTLE_BG,
    TEXT_MUTED,
    number_de,
)


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
        self.title("Nassheuer | Luftsteuerung" + (" | Simulation" if simulate else " | Hardware") + " | Korrektur 2")
        # Logische CTk-Maße: die bestehende DPI-Skalierung bleibt zuständig.
        self.geometry("800x420")
        self.minsize(800, 420)

        # Unter Windows beim ersten Anzeigen einmalig maximieren.
        if self.tk.call("tk", "windowingsystem") == "win32":
            self._maximize_pending = True

            def maximize_after_first_map(event):
                if event.widget is not self or not self._maximize_pending:
                    return
                self._maximize_pending = False
                self.after_idle(
                    lambda: self.state("zoomed")
                    if not (self.closing or self.shutdown_blocked) else None
                )

            self.bind("<Map>", maximize_after_first_map, add="+")
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

        # Gemeinsame, gut lesbare Buttonschrift und einheitliche Bedienhöhe.
        button_font = ctk.CTkFont(family="Segoe UI", size=12, weight="bold")
        for button in (self.output_button, self.clear_button, self.close_button):
            button.configure(font=button_font, height=40)
        self.clear_button.configure(text_color_disabled=TEXT_MUTED)
        self.close_button.configure(text_color_disabled="#B7C8D7")
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._log("Simulation: Startwerte 4 mA werden simuliert." if simulate else
                    "Hardwaremodus: FU, GLF und GHF werden beim Start auf 4 mA initialisiert.")
        self._log(f"Aktive Programmdatei: {__file__}")
        # Erst nach Widgetaufbau darf der Thread Ereignisse liefern.
        self.worker.start()
        # 50-ms-Tk-Poller und ca. 200-ms-Messpakete besitzen absichtlich getrennte Takte.
        self.poll_after_id = self.after(50, self.update_measurement)
    # Nur geänderte Darstellungsoptionen an Tk übertragen.
    def _configure_if_changed(self, widget, **options) -> None:
        changed = {
            key: value for key, value in options.items()
            if widget.cget(key) != value
        }
        if changed:
            widget.configure(**changed)

    # Erstellt einheitlich formatierte Beschriftungen.
    def _label(self, parent, text, *, size=13, bold=False, color=COLOR_DARK, **kwargs):
        return ctk.CTkLabel(parent, text=text, text_color=color,
                            font=ctk.CTkFont(family="Segoe UI", size=size, weight="bold" if bold else "normal"),
                            **kwargs)

    # Erstellt Kopfbereich, Reiter und den Schließen-Button.
    def _build_gui(self) -> None:
        # Kompakter Kopfbereich; Schließen bleibt auch ohne Scrollen erreichbar.
        header = ctk.CTkFrame(self, fg_color=CARD_BG, corner_radius=0)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        self._label(
            header, "NASSHEUER / LUFTSTEUERUNG",
            size=18, bold=True, height=28,
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(6, 0))
        mode = "SIMULATION · OHNE NI-ZUGRIFF" if self.simulate else "HARDWARE · MANUELL"
        self._label(
            header, mode, size=10, bold=True, height=28, width=190,
            color=COLOR_BLUE, fg_color="#E1F0F4", corner_radius=6,
        ).grid(row=0, column=1, padx=8, pady=(6, 0))
        self.close_button = ctk.CTkButton(
            header, text="Programm schließen", width=150,
            fg_color=COLOR_DARK, command=self.on_close,
        )
        self.close_button.grid(row=0, column=2, padx=(0, 12), pady=(6, 0))
        self.connection_label = self._label(
            header, "Messsignale: Initialisierung",
            size=10, height=18, color=COLOR_ORANGE, anchor="w",
        )
        self.connection_label.grid(
            row=1, column=0, columnspan=3, sticky="ew", padx=12, pady=(0, 4),
        )

        self.tabs = ctk.CTkTabview(
            self, fg_color="#F2F5F7", corner_radius=6,
            segmented_button_selected_color=COLOR_BLUE,
        )
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 6))
        for name in ("Steuerung", "Einstellungen", "Regelung", "Diagnose"):
            self.tabs.add(name)
        self._build_control_tab(self.tabs.tab("Steuerung"))
        self._build_settings_tab(self.tabs.tab("Einstellungen"))
        self._build_regulation_tab(self.tabs.tab("Regelung"))
        self.tabs.set("Steuerung")

    # Erstellt Eingabekarten, Messwertanzeige, Status und Ereignisprotokoll.
    def _build_control_tab(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        # Die Bedienansicht hat bewusst keinen Scrollbereich.
        content = ctk.CTkFrame(tab, fg_color="transparent")
        content.grid(row=0, column=0, sticky="nsew")
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(0, weight=1)

        self.measurement_cards = {}
        self.value_labels, self.current_labels, self.quality_labels = {}, {}, {}
        self._flow_font = ctk.CTkFont(family="Segoe UI", size=32, weight="bold")
        self.measurement_panel = ctk.CTkFrame(content, fg_color="transparent")
        self.measurement_panel.grid(row=0, column=0, sticky="nsew", pady=(0, 4))
        self.measurement_panel.grid_columnconfigure((0, 1), weight=1, uniform="sensors")
        self.measurement_panel.grid_rowconfigure(0, weight=1)
        self._measurement_card(self.measurement_panel, 0, "GHF", "High Flow", AI_GHF)
        self._measurement_card(self.measurement_panel, 1, "GLF", "Low Flow", AI_GLF)

        self.inputs, self.command_labels, self.entry_variables = {}, {}, {}
        self.actuator_cards = {}
        self.actuator_panel = ctk.CTkFrame(content, fg_color="transparent")
        self.actuator_panel.grid(row=1, column=0, sticky="ew", pady=(0, 4))
        self.actuator_panel.grid_columnconfigure((0, 1, 2), weight=1, uniform="actuators")
        for col, (name, subtitle) in enumerate((
            ("FU", "Frequenzumrichter"),
            ("GLF", "Gleitschieber Low Flow"),
            ("GHF", "Gleitschieber High Flow"),
        )):
            self._actuator_card(self.actuator_panel, col, name, subtitle)

        # Übernahme und Vorschau stehen fest unter den drei Eingabefeldern.
        actions = ctk.CTkFrame(content, fg_color=SUBTLE_BG, corner_radius=6)
        self.actions_panel = actions
        actions.grid(row=2, column=0, sticky="ew", pady=(0, 2))
        actions.grid_columnconfigure(0, weight=1)
        self.preview_label = self._label(
            actions, "FU-Stromvorschau: — mA", size=11, bold=True, height=18,
        )
        self.preview_label.grid(row=0, column=0, sticky="w", padx=8, pady=(4, 0))
        self.preview_hint = self._label(
            actions, "Rechnung ohne Ausgabe · 0–100 % · Leer = unverändert",
            size=9, height=16, color=TEXT_MUTED,
        )
        self.preview_hint.grid(row=1, column=0, sticky="w", padx=8, pady=(0, 4))
        self.clear_button = ctk.CTkButton(
            actions, text="Eingaben leeren", width=130, height=40,
            fg_color="#D7E3E9", text_color=COLOR_DARK,
            hover_color="#C7D7DF", command=self._clear_inputs,
        )
        self.clear_button.grid(row=0, column=1, rowspan=2, padx=(8, 6), pady=4)
        self.output_button = ctk.CTkButton(
            actions, text="Simulation übernehmen" if self.simulate else "Stellbefehle senden …",
            width=200, height=40, fg_color=COLOR_BLUE, hover_color="#0D5669",
            state="disabled", command=self.output_update,
        )
        self.output_button.grid(row=0, column=2, rowspan=2, padx=(0, 8), pady=4)
        self.output_status = self._label(
            content, "Noch kein Stellbefehl gesendet", size=11, height=20,
            color=TEXT_MUTED, anchor="w", justify="left", wraplength=620,
        )
        self.output_status.grid(row=3, column=0, sticky="ew", padx=4)

        # Technische Details und das Protokoll bekommen einen eigenen Reiter.
        diagnose = self.tabs.tab("Diagnose")
        diagnose.grid_columnconfigure(0, weight=1)
        diagnose.grid_rowconfigure(0, weight=1)
        details = ctk.CTkScrollableFrame(diagnose, fg_color="transparent")
        details.grid(row=0, column=0, sticky="nsew")
        details.grid_columnconfigure(0, weight=1)
        self._label(details, "DIAGNOSE UND PROTOKOLL", size=16, bold=True).grid(
            row=0, column=0, sticky="w", padx=10, pady=(8, 4),
        )
        self.measurement_status = self._label(
            details, self.measurement_message, color=COLOR_ORANGE,
            anchor="w", justify="left", wraplength=620,
        )
        self.measurement_status.grid(row=1, column=0, sticky="ew", padx=10, pady=4)
        self.task_label = self._label(
            details, "Tasks: noch nicht initialisiert",
            anchor="w", justify="left", wraplength=620,
        )
        self.task_label.grid(row=2, column=0, sticky="ew", padx=10, pady=4)
        self._label(
            details,
            "Task schließen ≠ Anlage abschalten. Keine sicherheitsgerichtete Steuerung.\n"
            "DAQ-Write bestätigt den Befehl; Ausgangsströme werden nicht rückgemessen.",
            color=TEXT_MUTED, anchor="w", justify="left", wraplength=620,
        ).grid(row=3, column=0, sticky="ew", padx=10, pady=8)
        self.log_box = ctk.CTkTextbox(
            details, height=220, fg_color=CARD_BG, text_color=COLOR_DARK,
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self.log_box.grid(row=4, column=0, sticky="ew", padx=10, pady=(4, 10))
        self.log_box.configure(state="disabled")

        self.bind("<Configure>", self._update_card_layout, add="+")
        self._update_card_layout()
        self.log_box = ctk.CTkTextbox(content, height=105, fg_color=CARD_BG,
                                      text_color=COLOR_DARK, font=ctk.CTkFont(family="Consolas", size=12))
        self.log_box.grid(row=10, column=0, columnspan=3, sticky="ew", padx=6, pady=(0, 8))
        self.log_box.configure(state="disabled")

        # Nur Layoutänderungen auslösen; bestehende Tk-Bindungen erhalten.
        self._cards_compact = None
        self.bind("<Configure>", self._update_card_layout, add="+")
        self._update_card_layout()

    # Ordnet vorhandene Karten neu an, ohne Widgets oder Eingaben neu anzulegen.
    # Kleine Fenster behalten dieselbe Anordnung; nur die große Zahl skaliert.
    def _update_card_layout(self, event=None) -> None:
        if event is not None and event.widget is not self:
            return
        if self.closing or self.shutdown_blocked:
            return
        # CTk liefert logische Größen; negative Monitorpositionen berücksichtigen.
        dimensions = self.geometry().split("+", 1)[0].split("-", 1)[0]
        height = int(dimensions.split("x", 1)[1])
        font_size = 48 if height >= 650 else 32
        if self._flow_font.cget("size") != font_size:
            self._flow_font.configure(size=font_size)

    # Erstellt das Eingabefeld und die Befehlsanzeige für einen Ausgang.
    def _actuator_card(self, parent, column: int, name: str, subtitle: str) -> None:
        card = ctk.CTkFrame(
            parent, fg_color=CARD_BG, corner_radius=6,
            border_width=1, border_color="#D7E3E9",
        )
        card.grid(row=0, column=column, sticky="nsew", padx=3)
        card.grid_columnconfigure(0, weight=1)
        self.actuator_cards[name] = card
        available = name in CONFIGURED_ACTUATOR_CURVES
        self._label(card, name, size=14, bold=True, height=18).grid(
            row=0, column=0, sticky="w", padx=8, pady=(4, 0),
        )
        curve = "0 % = 4 mA · 100 % = 20 mA" if name == "FU" else "0 % = 20 mA · 100 % = 4 mA"
        self._label(
            card, curve if available else "Kennlinie fehlt",
            size=10, height=16, color=TEXT_MUTED if available else COLOR_ORANGE,
        ).grid(row=1, column=0, sticky="w", padx=8)

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.grid(row=2, column=0, sticky="ew", padx=8, pady=2)
        row.grid_columnconfigure(1, weight=1)
        self._label(row, "Sollwert", size=11, height=18).grid(
            row=0, column=0, padx=(0, 5),
        )
        var = ctk.StringVar(master=self, value="")
        self.entry_variables[name] = var
        entry = ctk.CTkEntry(
            row, width=70, height=30, textvariable=var,
            fg_color="#F7F9FB", text_color=COLOR_DARK,
            font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"),
        )
        entry.grid(row=0, column=1, sticky="ew")
        self._label(row, "%", size=12, height=18).grid(row=0, column=2, padx=(5, 0))
        if not available:
            entry.configure(state="disabled")
        self.inputs[name] = entry
        if name == "FU":
            self.fu_variable = var
            var.trace_add("write", lambda *_: self._update_preview())
        # Platz für die vorhandene dreizeilige Befehlsbestätigung reservieren.
        label = self._label(
            card, "Letzter Befehl: —", size=10, height=36,
            color=TEXT_MUTED, anchor="w", justify="left", wraplength=205,
        )
        label.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 4))
        self.command_labels[name] = label

    def _measurement_card(self, parent, column, name, title, channel) -> None:
        card = ctk.CTkFrame(
            parent, fg_color=CARD_BG, corner_radius=6,
            border_width=1, border_color="#D7E3E9",
        )
        card.grid(row=0, column=column, sticky="nsew", padx=3)
        card.grid_columnconfigure(1, weight=1)
        card.grid_rowconfigure(0, weight=1)
        self.measurement_cards[name] = card
        self._label(card, f"{name}\n{title}", size=11, bold=True, height=32).grid(
            row=0, column=0, sticky="w", padx=(8, 4), pady=(4, 0),
        )
        value = ctk.CTkLabel(
            card, text="—", font=self._flow_font,
            text_color=COLOR_DARK, height=38,
        )
        value.grid(row=0, column=1, sticky="e", padx=4, pady=(4, 0))
        self._label(card, "m³/h", size=11, height=18, color=TEXT_MUTED).grid(
            row=0, column=2, sticky="e", padx=(0, 8),
        )
        current = self._label(
            card, "Sensorstrom: — mA", size=10, height=16, color=TEXT_MUTED,
        )
        current.grid(row=1, column=0, columnspan=3, sticky="w", padx=8)
        quality = self._label(
            card, "Warte auf Messdaten", size=10, height=20, color=COLOR_ORANGE,
            wraplength=320, anchor="w", justify="left",
        )
        quality.grid(row=2, column=0, columnspan=3, sticky="ew", padx=8, pady=(0, 4))
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
        # Reine Informationsanzeige: keine Änderung an DAQ-Tasks oder Grenzwerten.
        self._label(
            card,
            f"Messung: NI-9253 · {SAMPLE_RATE_HZ:.0f} Samples/s je Kanal angefordert\n"
            "AI-Task: 3,5–20,0 mA im Code angefordert\n"
            f"Sensor-Nullpunkt: {number_de(SENSOR_ZERO_A * 1000, 1)} mA\n"
            f"Software-Plausibilitätsbereich: {number_de(SENSOR_VALID_MIN_A * 1000, 1)}–"
            f"{number_de(SENSOR_VALID_MAX_A * 1000, 1)} mA\n"
            f"Messzyklus: ca. {GUI_REFRESH_MS} ms zuzüglich Lesezeit · "
            f"veraltet nach {number_de(MAX_MEASUREMENT_AGE_S, 1)} s\n\n"
            f"AO-Task: {number_de(MIN_OUTPUT_A * 1000, 1)}–"
            f"{number_de(MAX_OUTPUT_A * 1000, 1)} mA konfiguriert\n"
            "FU: 0 % = 4 mA · 100 % = 20 mA\n"
            "GLF/GHF: 0 % = 20 mA · 100 % = 4 mA\n\n"
            "Im Hardwaremodus werden beim Start und beim bestätigten Schließen "
            "jeweils 4 mA an FU, GLF und GHF angefordert.\n"
            "DAQ-Write bestätigt den Befehl; der Ausgangsstrom wird nicht rückgemessen.",
            justify="left", anchor="w", wraplength=620,
            color=TEXT_MUTED,
        ).pack(anchor="w", padx=20, pady=20)

    # Zeigt an, dass noch kein automatischer Regler eingebaut ist.
    def _build_regulation_tab(self, tab) -> None:
        card = ctk.CTkFrame(tab, fg_color=CARD_BG)
        card.pack(fill="both", expand=True, padx=8, pady=8)
        self._label(card, "Regelung", size=22, bold=True).pack(anchor="w", padx=24, pady=(24, 8))
        self._label(card, "Nicht implementiert", bold=True, color=COLOR_ORANGE).pack(anchor="w", padx=24)
        self._label(card, "Die Quelldateien enthalten keinen automatischen Regler.\n"
                    "Diese Version unterstützt manuelle Stellbefehle und die Durchflussanzeige.\n"
                    "Simulierte Sensorwerte sind kein Modell der realen Luftstrecke.",
                    justify="left", color=TEXT_MUTED, wraplength=620).pack(anchor="w", padx=24, pady=14)

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
        editing = not (
            self.closing or self.shutdown_blocked
            or self.confirming or self.output_busy
        )
        # Sensorwarnungen und frühere Ausgabefehler sperren die manuelle
        # Korrektur nicht. Werte und Kennlinien werden weiterhin validiert.
        self._configure_if_changed(
            self.output_button, state="normal" if editing else "disabled",
            fg_color="#008C83" if editing else "#DCE5EB",
            text_color="#FFFFFF", text_color_disabled=TEXT_MUTED,
        )
        self._configure_if_changed(
            self.clear_button, state="normal" if editing else "disabled")

        for name, entry in self.inputs.items():
            self._configure_if_changed(
                entry,
                state="normal" if editing and name in CONFIGURED_ACTUATOR_CURVES
                else "disabled",
            )

        if hasattr(self, "reconnect_button"):
            self._configure_if_changed(
                self.reconnect_button,
                state="normal" if editing else "disabled",
            )

        self._configure_if_changed(
            self.close_button,
            state="disabled" if self.confirming or self.closing else "normal",
        )

    # Aktualisiert Messwerte und kennzeichnet ungültige oder veraltete Daten.
    def _render_measurement(self) -> None:
        now = time.monotonic()

        if not hasattr(self, "_last_display_readings"):
            self._last_display_readings = {}

        self._display_times = {}
        self._display_cached = {}
        summaries = []
        valid_count = 0

        for name, convert in (
            ("GHF", ghf_amp_to_m3h),
            ("GLF", glf_amp_to_m3h),
        ):
            reading = self.sensor_readings[name]
            finite = (
                reading.current_a is not None
                and math.isfinite(reading.current_a)
            )

            # Auch endliche Bereichsfehler mit Originalzeitstempel speichern.
            # Ein Fehler wird dadurch nicht zu einem gültigen Messwert.
            if finite and reading.received_at:
                self._last_display_readings[name] = reading

            shown = self._last_display_readings.get(name)
            self._display_times[name] = shown.received_at if shown else 0.0

            cached = shown is not None and (
                shown is not reading
                or not reading.recent(now)
                or self.measurement_error
                or self.closing
                or self.shutdown_blocked
            )
            self._display_cached[name] = cached

            # Endliche Sensorwerte immer umrechnen und anzeigen.
            # Außerhalb der Kennlinie ist das nur ein ungeprüfter Rechenwert.
            value_text = "—"
            if shown is not None:
                calculated = convert(shown.current_a)
                if math.isfinite(calculated):
                    value_text = number_de(calculated)

            raw = reading.current_a
            if raw is None and shown is not None:
                raw = shown.current_a

            if raw is None:
                raw_text = "—"
            elif math.isfinite(raw):
                raw_text = number_de(
                    raw * 1000, 3 if reading.valid else 6)
            else:
                raw_text = (
                    "NaN" if math.isnan(raw)
                    else "+∞" if raw > 0
                    else "−∞"
                )

            color = COLOR_ORANGE

            if self.closing or self.shutdown_blocked:
                quality, summary = "Abschluss aktiv", "Abschluss"

            elif self.measurement_error:
                quality = "Messfehler · Erfassung beendet"
                summary, color = "Messfehler", COLOR_RED

            elif not reading.received_at:
                quality, summary = "Warte auf neue Messdaten", "wartet"

            elif not reading.recent(now):
                quality, summary = "Keine aktuellen Messdaten", "veraltet"

            elif reading.current_a is None:
                quality = reading.problem or "Messwert fehlt"
                summary, color = "fehlt", COLOR_RED

            elif not reading.valid or value_text == "—":
                quality = "Fehler: " + (
                    reading.problem or "Messwert nicht berechenbar")
                quality += " · Anzeige ist ein ungeprüfter Rechenwert"
                summary, color = "Fehler", COLOR_RED

            else:
                quality = (
                    "Simulierter Wert" if self.simulate
                    else "Gültiges Stromsignal"
                )
                summary = "gültig"
                color = COLOR_BLUE if self.simulate else COLOR_GREEN
                valid_count += 1

            if cached:
                quality += " · Letzter empfangener Rechenwert"
                if shown is not None and not shown.valid:
                    quality += " · bereits beim Empfang ungültig"

            self._configure_if_changed(
                self.value_labels[name],
                text=value_text,
                text_color=COLOR_DARK if summary == "gültig" else color,
            )
            self._configure_if_changed(
                self.current_labels[name],
                text="Sensorstrom: " + raw_text + " mA",
                text_color=color
                if summary in ("Fehler", "Messfehler", "fehlt")
                else TEXT_MUTED,
            )
            self._configure_if_changed(
                self.quality_labels[name],
                text=quality,
                text_color=color,
            )
            summaries.append(f"{name}: {summary}")

        summary_text = " · ".join(summaries)
        summary_color = (
            (COLOR_BLUE if self.simulate else COLOR_GREEN)
            if valid_count == 2 else COLOR_ORANGE
        )
        if self.measurement_error:
            summary_color = COLOR_RED

        self._configure_if_changed(
            self.measurement_status,
            text=summary_text,
            text_color=summary_color,
        )
        self._configure_if_changed(
            self.connection_label,
            text=("SIMULIERT · " if self.simulate else "") +
                summary_text.upper(),
            text_color=summary_color,
        )

        outputs = ", ".join(self.daq_state.output_tasks) or "keine"
        self._configure_if_changed(
            self.task_label,
            text=("SIMULATION · " if self.simulate else "") +
                f"Mess-Task: "
                f"{'vorhanden' if self.daq_state.measurement_task else 'nicht vorhanden'}\n"
                f"Ausgabe-Tasks: {outputs}\n"
                f"Ausgabefehler: "
                f"{'vorhanden · neuer Bedienversuch möglich' if self.daq_state.output_fault else 'keiner gemeldet'}",
            text_color=COLOR_RED
            if self.daq_state.output_fault else TEXT_MUTED,
        )
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
        """GUI-Aktualisierung auch nach Darstellungsfehlern fortsetzen."""
        previous = self.poll_after_id
        self.poll_after_id = None

        # Eine vorhandene Planung ersetzen, keinen zweiten Poller erzeugen.
        if previous is not None:
            try:
                self.after_cancel(previous)
            except tk.TclError:
                pass

        delay = 50
        cycle_ok = True
        try:
            self._process_measurement_events()
        except Exception:
            import traceback
            cycle_ok = False
            delay = 500
            self._report_gui_error(traceback.format_exc())
        finally:
            try:
                alive = bool(self.winfo_exists())
            except tk.TclError:
                alive = False

            if alive:
                try:
                    # Busy nicht zurücksetzen: Ein DAQ-Auftrag könnte noch laufen.
                    self._refresh_controls()
                except Exception:
                    import traceback
                    cycle_ok = False
                    delay = 500
                    self._report_gui_error(traceback.format_exc())
                finally:
                    if cycle_ok:
                        self._last_gui_error = None
                    self.poll_after_id = self.after(
                        delay, self.update_measurement)

    def _report_gui_error(self, detail: str) -> None:
        # Gleiche Dauerfehler nicht ständig erneut in den Vordergrund holen.
        if detail == getattr(self, "_last_gui_error", None):
            return

        self._last_gui_error = detail
        print(detail)

        try:
            self._log("GUI-Fehler: " + detail.strip().splitlines()[-1])
        except Exception:
            pass

        try:
            self._show_error_details(
                "Fehler in der Oberfläche",
                detail + "\nDie Aktualisierung wird erneut versucht. "
                "Ein laufender Hardwareauftrag wird dabei nicht abgebrochen.")
        except Exception:
            import traceback
            traceback.print_exc()

    def _process_measurement_events(self) -> None:
        """Verarbeitet Ereignisse; die Zeitplanung übernimmt update_measurement()."""
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
            elif kind == "measurement_restarting":
                self.measurement_valid = False
                self.measurement_error = False
                self.last_measurement_at = 0.0
                self.sensor_readings = {
                    name: SensorReading() for name in SENSOR_CHANNELS
                }
                self.measurement_message = "Messung wird neu verbunden."

            elif kind == "measurement_error":
                self.measurement_valid = False
                self.measurement_error = True
                self.sensor_readings = {
                    name: SensorReading(problem="DAQ-Messfehler")
                    for name in SENSOR_CHANNELS
                }
                self.measurement_message = str(payload)
                self._log(str(payload))
                self._show_error_details(
                    "Messung fehlgeschlagen",
                    f"GHF: {AI_GHF}\nGLF: {AI_GLF}\n\n{payload}\n\n"
                    "Messkanäle, Versorgung und Verbindung prüfen. Danach "
                    "'Messung neu verbinden' drücken. Manuelle Stellbefehle "
                    "bleiben nach Bestätigung möglich.",
                )

            elif kind == "measurement_restart_done":
                self.output_busy = False
                self.output_status.configure(
                    text="Messneustart fehlgeschlagen · Details beachten"
                    if self.measurement_error else
                    "Messung gestartet · warte auf neue Messwerte",
                    text_color=COLOR_RED if self.measurement_error else COLOR_BLUE,
                )

            elif kind == "started":
                self.measurement_error = False
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
                self.output_status.configure(
                    text="Ausgabefehler · Eingaben korrigieren und erneut senden",
                    text_color=COLOR_RED,
                )
                confirmed = ", ".join(n for n, _, _ in successful) or "keine"
                self._log("Ausgabefehler: " + detail + " | Bestätigt: " + confirmed)
                self._show_error_details(
                    "Stellbefehl fehlgeschlagen",
                    f"{detail}\n\nBestätigte Teilbefehle: {confirmed}\n"
                    "Der tatsächliche Ausgangszustand ist nicht gemessen.\n\n"
                    "Kanaleinstellung/Verbindung prüfen, Sollwerte korrigieren "
                    "und erneut senden. Frühere Teilaufträge werden nicht "
                    "automatisch wiederholt.",
                )
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
        self._notify_sensor_errors()
        # 50-ms-Tk-Poller und ca. 200-ms-Messpakete besitzen absichtlich getrennte Takte.
        # Den nächsten Aufruf plant ausschließlich update_measurement().

    # Prüft die Sollwerte, fragt nach Bestätigung und übergibt sie dem Worker.
    def output_update(self) -> None:
        if self.confirming or self.output_busy or self.closing or self.shutdown_blocked:
            return
        try:
            commands = prepare_commands({
                name: entry.get() for name, entry in self.inputs.items()
            })
            if not commands:
                self.output_status.configure(
                    text="Keine Sollwerte eingegeben", text_color=TEXT_MUTED)
                return
        except (ValueError, RuntimeError) as exc:
            self.output_status.configure(text=str(exc), text_color=COLOR_RED)
            self._log("Eingabe korrigieren: " + str(exc))
            self._show_error_details("Eingabefehler", str(exc))
            return

        warnings = []
        now = time.monotonic()
        if self.measurement_error:
            warnings.append("Messung: " + self.measurement_message)

        # Warnungen anzeigen, ohne die manuelle Korrektur zu verriegeln.
        for name, reading in self.sensor_readings.items():
            if reading.valid and reading.recent(now) and not self.measurement_error:
                continue
            raw = "fehlt" if reading.current_a is None else (
                number_de(reading.current_a * 1000, 6) + " mA")
            age = "kein Empfang" if not reading.received_at else (
                f"Alter {max(0, round((now - reading.received_at) * 1000))} ms")
            reason = reading.problem or (
                "Messwert veraltet" if not reading.recent(now)
                else "keine gültige Sensorüberwachung")
            warnings.append(
                f"{name} / {SENSOR_CHANNELS[name]}: {raw}; {age}; {reason}")

        if self.daq_state.output_fault:
            warnings.append(
                "Vorheriger Ausgangsfehler: Ausgangszustand nicht gemessen. "
                "Ein betroffener Ausgangs-Task wird beim neuen Auftrag "
                "zuerst freigegeben und neu vorbereitet.")

        details = "\n".join(
            f"{name}: {number_de(percent)} % → "
            f"{number_de(current * 1000, 3)} mA"
            for name, percent, current in commands)
        warning_text = (
            "\n\nAKTUELLE WARNUNGEN:\n" + "\n".join(warnings) +
            "\n\nTrotz dieser Hinweise manuell senden?"
            if warnings else ""
        )

        self.confirming = True
        self._refresh_controls()
        confirmed = False
        try:
            confirmed = messagebox.askyesno(
                "Simulation bestätigen" if self.simulate else
                "Manuelle Stromausgabe bestätigen",
                ("Simulation, keine realen Ausgänge.\n\n" if self.simulate
                 else "Folgende manuelle Stellbefehle senden?\n\n") +
                details +
                "\n\nSensorwarnungen verriegeln diesen manuellen Auftrag nicht." +
                warning_text +
                "\n\nDAQ-Bestätigung ist keine Messung des Ausgangsstroms.",
                parent=self,
            )
        finally:
            self.confirming = False
            self._refresh_controls()

        # Fehler während des Bestätigungsdialogs erst danach anzeigen.
        pending = getattr(self, "_pending_error", None)
        if pending is not None:
            self._pending_error = None
            self._show_error_details(*pending)

        if not confirmed:
            self.output_status.configure(
                text="Befehl abgebrochen", text_color=TEXT_MUTED)
        elif self.closing or self.shutdown_blocked or self.output_busy:
            self.output_status.configure(
                text="Auftrag nicht gesendet · anderer Vorgang aktiv",
                text_color=COLOR_ORANGE)
        else:
            self.output_busy = self.worker.request_write(commands)
            self.output_status.configure(
                text="Stellbefehl angefordert · warte auf DAQ-Bestätigung"
                if self.output_busy else "Auftrag nicht angenommen · erneut versuchen",
                text_color=COLOR_ORANGE)
        self._refresh_controls()

    def _restart_measurement(self) -> None:
        """Buttonaktion; der Worker übernimmt alle Treiberzugriffe."""
        if self.confirming or self.output_busy or self.closing or self.shutdown_blocked:
            return
        self.output_busy = self.worker.request_restart_measurement()
        self.output_status.configure(
            text="Messung wird neu verbunden · Ausgänge werden nicht neu gesetzt"
            if self.output_busy else "Messneustart nicht angenommen · erneut versuchen",
            text_color=COLOR_ORANGE)
        self._refresh_controls()

    def _show_error_details(self, title: str, detail: str) -> None:
        """Normaler Fehlerdialog mit OK, wie bei der Sollwertbestätigung."""
        if self.closing:
            return
        if self.confirming:
            # Keine Fehlerfenster über einen vorhandenen Dialog legen.
            self._pending_error = (title, detail)
            return

        # Sehr lange Meldungen im Terminal vollständig erhalten.
        text = detail
        if len(text) > 1200:
            print(title + "\n" + detail)
            text = text[:500] + "\n…\n" + text[-500:]
            text += "\n\nVollständige Meldung im Terminal."

        self.confirming = True
        try:
            self._refresh_controls()
            messagebox.showerror(
                title,
                text + "\n\nMit OK quittieren. Danach kannst du "
                "Einstellungen ändern und erneut versuchen.",
                parent=self,
            )
        finally:
            # Nach OK die Bedienung wieder freigeben.
            self.confirming = False
            self._refresh_controls()

        pending = getattr(self, "_pending_error", None)
        if pending is not None:
            self._pending_error = None
            self._show_error_details(*pending)

    def _notify_sensor_errors(self) -> None:
        """Aktuelle Sensorfehler einmal melden, nicht bei jedem Messpaket."""
        if (
            self.confirming or self.closing
            or self.shutdown_blocked or self.measurement_error
        ):
            return

        if not hasattr(self, "_sensor_popup_problem"):
            self._sensor_popup_problem = {}
            self._sensor_popup_clear_since = {}
            self._sensor_popup_last_seen = {}

        now = time.monotonic()
        alerts = []
        new_error = False

        for name, reading in self.sensor_readings.items():
            old_time = self._sensor_popup_last_seen.get(name, 0.0)

            # Eine Empfangspause unterbricht die fehlerfreie Beobachtungszeit.
            if (
                old_time
                and reading.received_at - old_time > MAX_MEASUREMENT_AGE_S
            ):
                self._sensor_popup_clear_since.pop(name, None)

            self._sensor_popup_last_seen[name] = reading.received_at

            # Alte Zwischenwerte aus einer aufgestauten Queue nicht melden.
            if not reading.recent(now):
                self._sensor_popup_clear_since.pop(name, None)
                continue

            if reading.valid:
                since = self._sensor_popup_clear_since.setdefault(name, now)

                # Erst nach stabiler Erholung denselben Fehler erneut melden.
                # Dadurch erzeugt Grenzpendeln keine ständigen Dialoge.
                if now - since >= 3.0:
                    self._sensor_popup_problem.pop(name, None)
                continue

            self._sensor_popup_clear_since.pop(name, None)

            if reading.current_a is None:
                # Ein leerer Startpuffer ist noch kein Bereichsfehler.
                continue

            problem = reading.problem or "Ungültiger Sensorstrom"
            alerts.append(
                f"{name}: {problem}\n"
                f"Kanal: {SENSOR_CHANNELS[name]}\n"
                f"Sensorstrom: "
                f"{number_de(reading.current_a * 1000, 6)} mA"
            )

            # Nach Kanal und Fehlergrund unterscheiden, nicht nach jedem µA.
            if self._sensor_popup_problem.get(name) != problem:
                self._sensor_popup_problem[name] = problem
                new_error = True

        if new_error:
            self._show_error_details(
                "Sensorfehler",
                "\n\n".join(alerts) +
                "\n\nDer Istwert bleibt mit Fehlerhinweis sichtbar. "
                "Manuelle Korrekturen bleiben möglich.",
            )

    # Fragt bei Bedarf nach der Anlagensicherung und fordert die Task-Freigabe an.
    def on_close(self) -> None:
        if self.closing or self.confirming:
            return
        self.confirming = True
        self._refresh_controls()
        try:
            # Ausstehender Write kann vom Worker bereits übernommen worden sein.
            if not self.simulate:
                secured = messagebox.askyesno(
                    "Mit 4-mA-Endwerten schließen?",
                    "Beim Schließen werden folgende Sollwerte gesendet:\n\n"
                    "FU: 0 % = 4 mA\n"
                    "GLF: 100 % = 4 mA\n"
                    "GHF: 100 % = 4 mA\n\n"
                    "Endwerte senden und anschließend schließen?",
                    parent=self,
                )
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
        self.output_status.configure(
            text="4-mA-Endwerte werden gesendet; danach werden Tasks freigegeben",
            text_color=COLOR_ORANGE,
        )
        self._refresh_controls()
