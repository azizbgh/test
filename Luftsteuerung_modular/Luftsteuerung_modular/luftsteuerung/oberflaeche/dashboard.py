"""Die vorhandene Dashboardansicht mit Navigation und Verlaufskurven.

LuftsteuerungDashboard ist weiterhin die beim Programmstart gewählte Klasse.
Sie erbt von LuftsteuerungApp und verwendet deren Bedien- und DAQ-Abläufe.
Diese Datei enthält die dazugehörigen Widgets, Größenanpassungen, Sollwertpfeile,
Messwertalter und Canvasdiagramme. Die Kurven verwenden vorhandene Messpakete;
es entstehen keine zusätzlichen Hardwareabfragen.

Gleich benannte Methoden in Basis und Dashboard sind gezielte Überschreibungen:
super() führt an den ursprünglichen Stellen die gemeinsame Basislogik aus.
Diese Methoden sind daher keine unnötigen Kopien und werden nicht entfernt.
"""

from __future__ import annotations

from collections import deque
import time
import tkinter as tk
from tkinter import font as tkfont

import customtkinter as ctk

from ..konfiguration import (
    AI_GHF,
    AI_GLF,
    AO_CHANNELS,
    GUI_REFRESH_MS,
    MAX_MEASUREMENT_AGE_S,
    SENSOR_VALID_MAX_A,
    SENSOR_VALID_MIN_A,
)
from ..kennlinien import ghf_amp_to_m3h, glf_amp_to_m3h, parse_percent
from .basis import LuftsteuerungApp
from .darstellung import (
    CARD_BG,
    COLOR_BLUE,
    COLOR_DARK,
    COLOR_ORANGE,
    COLOR_RED,
    TEXT_MUTED,
    number_de,
)


class LuftsteuerungDashboard(LuftsteuerungApp):
    """Ansicht B mit drei Seiten, Messwertalter und zwei Verlaufskurven."""

    def __init__(self, *, simulate=True, controller=None):
        # Die Diagramme verwenden bereits empfangene Messpakete.
        # Es werden dafür keine zusätzlichen Hardwareabfragen gestartet.
        self._history = {
            name: deque(maxlen=1200)
            for name in ("GHF", "GLF")
        }
        self._recorded = {"GHF": 0.0, "GLF": 0.0}
        self._plot_at = 0.0
        self._graphs_dirty = True
        self._density = None

        # Die vorhandene Basisklasse übernimmt weiterhin:
        # Worker, Hardwareinitialisierung, Bestätigungen und Programmabschluss.
        super().__init__(simulate=simulate, controller=controller)
        self._fit_dashboard(force=True)

    def _text(self, parent, text, role="body", **kwargs):
        """Erstellt eine Beschriftung mit einer gemeinsamen Schriftvorlage."""
        return ctk.CTkLabel(
            parent,
            text=text,
            font=self._fonts[role],
            text_color=kwargs.pop("text_color", COLOR_DARK),
            **kwargs,
        )

    def _panel(self, parent):
        """Gemeinsames Aussehen der Messkarten und Bedienbereiche."""
        return ctk.CTkFrame(
            parent,
            fg_color=CARD_BG,
            corner_radius=8,
            border_width=1,
            border_color="#D7E3E9",
        )

    def _build_gui(self):
        # Gemeinsame Schriftobjekte lassen sich beim Verkleinern des Fensters
        # anpassen, ohne sämtliche Widgets neu aufzubauen.
        self._fonts = {
            "title": ctk.CTkFont(
                family="Segoe UI", size=18, weight="bold"
            ),
            "heading": ctk.CTkFont(
                family="Segoe UI", size=14, weight="bold"
            ),
            "value": ctk.CTkFont(
                family="Segoe UI", size=28, weight="bold"
            ),
            "body": ctk.CTkFont(
                family="Segoe UI", size=11
            ),
            "small": ctk.CTkFont(
                family="Segoe UI", size=10
            ),
            "entry": ctk.CTkFont(
                family="Segoe UI", size=16, weight="bold"
            ),
            "button": ctk.CTkFont(
                family="Segoe UI", size=12, weight="bold"
            ),
            "unit": ctk.CTkFont(
                family="Segoe UI", size=24, weight="bold"
            ),
            "strong": ctk.CTkFont(
                family="Segoe UI", size=12, weight="bold"
            ),
            "channel": ctk.CTkFont(
                family="Segoe UI", size=18, weight="bold"
            ),
            "command": ctk.CTkFont(
                family="Segoe UI", size=14
            ),
            "table_header": ctk.CTkFont(
                family="Segoe UI", size=11, weight="bold"
            ),
        }

        # Negative Tk-Schriftgrößen bedeuten Pixel.
        self._axis_font = tkfont.Font(
            family="Segoe UI", size=-11
        )
        self._badge_font = tkfont.Font(
            family="Segoe UI", size=-12, weight="bold"
        )

        self.configure(fg_color="#F6F9FD")

        # Kopfzeile: Marke, Betriebsart und die drei gewünschten Seiten.
        self.header = ctk.CTkFrame(
            self,
            fg_color=CARD_BG,
            corner_radius=0,
        )
        self.header.grid(row=0, column=0, sticky="ew")
        self.header.grid_columnconfigure(0, weight=1)

        self._text(
            self.header,
            "NASSHEUER / LUFTSTEUERUNG",
            "title",
            height=28,
        ).grid(
            row=0, column=0, sticky="w", padx=12, pady=6
        )

        self._text(
            self.header,
            "SIMULATION" if self.simulate else "HARDWARE",
            "button",
            height=28,
            width=100,
            corner_radius=6,
            fg_color="#DFF0F7",
            text_color=COLOR_BLUE,
        ).grid(
            row=0, column=1, padx=8, pady=6
        )

        self.nav = ctk.CTkFrame(
            self.header,
            fg_color="transparent",
        )
        self.nav.grid(row=0, column=2, padx=8, pady=4)

        self.pages = {}
        self.nav_buttons = {}

        for index, name in enumerate(
            ("Steuerung", "Regelung", "Einstellungen")
        ):
            page = ctk.CTkFrame(
                self,
                fg_color="transparent",
            )
            self.pages[name] = page

            button = ctk.CTkButton(
                self.nav,
                text=name,
                width=90,
                height=30,
                font=self._fonts["button"],
                corner_radius=6,
                command=lambda selected=name: self._show_page(selected),
            )
            button.grid(row=0, column=index, padx=2)
            self.nav_buttons[name] = button

        # Diese Widgetverzeichnisse werden auch von den vorhandenen
        # Bedien- und Messfunktionen der Basisklasse verwendet.
        self.value_labels = {}
        self.current_labels = {}
        self.age_labels = {}
        self.quality_labels = {}
        self.command_labels = {}
        self.inputs = {}
        self.entry_variables = {}
        self.graphs = {}

        self._build_control_tab(self.pages["Steuerung"])
        self._build_regulation_tab(self.pages["Regelung"])
        self._build_settings_tab(self.pages["Einstellungen"])

        # Untere Statuszeile mit dem vorhandenen Abschlussknopf.
        footer = ctk.CTkFrame(
            self,
            fg_color="transparent",
        )
        footer.grid(
            row=2, column=0, sticky="ew", padx=12, pady=(0, 6)
        )
        footer.grid_columnconfigure(0, weight=1)

        self.connection_label = self._text(
            footer,
            "Initialisierung",
            "small",
            anchor="w",
            height=18,
        )
        self.connection_label.grid(
            row=0, column=0, sticky="w"
        )

        self.close_button = ctk.CTkButton(
            footer,
            text="Programm schließen",
            width=150,
            font=self._fonts["button"],
            fg_color="#F9FBFE",
            border_width=1,
            border_color="#8799AD",
            hover_color="#E4EDF6",
            text_color=COLOR_DARK,
            command=self.on_close,
        )
        self.reconnect_button = ctk.CTkButton(
            footer, text="Messung neu verbinden", width=170, height=28,
            font=self._fonts["button"], fg_color="#E1EDF4",
            hover_color="#D5E5EE", text_color=COLOR_DARK,
            command=self._restart_measurement,
        )
        self.reconnect_button.grid(row=0, column=1, padx=(8, 0))
        self.close_button.grid(row=0, column=2, padx=(8, 0))

        self.bind("<Configure>", self._fit_dashboard, add="+")
        self._show_page("Steuerung")
        self._fit_dashboard(force=True)

    def _show_page(self, name):
        """Wechselt die Ansicht, ohne den DAQ-Worker neu zu starten."""
        for key, page in self.pages.items():
            if key == name:
                page.grid(
                    row=1,
                    column=0,
                    sticky="nsew",
                    padx=10,
                    pady=4,
                )
            else:
                page.grid_remove()

            self.nav_buttons[key].configure(
                fg_color="#E1EDF4" if key == name else "transparent",
                hover_color="#D5E5EE",
                text_color=COLOR_DARK,
            )

        self._graphs_dirty = True

    def _build_control_tab(self, tab):
        # Die gemeinsame uniform-Gruppe begrenzt das Verhältnis zwischen
        # oberem Bedienbereich und den unteren Diagrammen.
        self._control_page = tab
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(
            0, weight=11, uniform="sections"
        )
        tab.grid_rowconfigure(
            1, weight=9, uniform="sections"
        )

        upper = ctk.CTkFrame(
            tab,
            fg_color="transparent",
        )
        upper.grid(
            row=0, column=0, sticky="nsew", pady=(0, 8)
        )
        upper.grid_columnconfigure(
            0, weight=27, uniform="workspace"
        )
        upper.grid_columnconfigure(
            1, weight=73, uniform="workspace"
        )
        upper.grid_rowconfigure(0, weight=1)

        # Linke Spalte: zwei gleich große Messkarten.
        readings = ctk.CTkFrame(
            upper,
            fg_color="transparent",
        )
        readings.grid(
            row=0, column=0, sticky="nsew", padx=(0, 8)
        )
        readings.grid_columnconfigure(0, weight=1)
        readings.grid_rowconfigure(
            (0, 1), weight=1, uniform="readings"
        )

        for row, name in enumerate(("GHF", "GLF")):
            card = self._panel(readings)
            card.configure(fg_color="#EDF4FA")
            card.grid(
                row=row,
                column=0,
                sticky="nsew",
                pady=(0, 4) if row == 0 else (4, 0),
            )
            card.grid_columnconfigure(0, weight=1)

            # Zusätzlicher Platz bleibt unterhalb des zusammengehörenden
            # Inhalts. Zahl und Einheit werden nicht auseinandergezogen.
            card.grid_rowconfigure(4, weight=1)

            self._text(
                card,
                f"{name} – Volumenstrom",
                "heading",
                height=20,
            ).grid(
                row=0,
                column=0,
                sticky="w",
                padx=10,
                pady=(4, 0),
            )

            number_row = ctk.CTkFrame(
                card,
                fg_color="transparent",
            )
            number_row.grid(
                row=1,
                column=0,
                sticky="w",
                padx=12,
                pady=(0, 2),
            )

            value = self._text(
                number_row,
                "—",
                "value",
                height=26,
            )
            value.grid(row=0, column=0, sticky="w")
            self.value_labels[name] = value

            # Die Einheit steht unmittelbar neben dem Messwert.
            self._text(
                number_row,
                "m³/h",
                "unit",
                height=24,
            ).grid(
                row=0,
                column=1,
                sticky="s",
                padx=(5, 0),
            )

            details = ctk.CTkFrame(
                card,
                fg_color="transparent",
            )
            details.grid(
                row=2, column=0, sticky="w", padx=12
            )

            self._text(
                details,
                "Sensorstrom:",
                "body",
                height=18,
            ).grid(
                row=0,
                column=0,
                sticky="w",
                padx=(0, 8),
            )

            current = self._text(
                details,
                "— mA",
                "strong",
                height=18,
            )
            current.grid(row=0, column=1, sticky="w")
            self.current_labels[name] = current

            age_row = ctk.CTkFrame(
                card,
                fg_color="transparent",
            )
            age_row.grid(
                row=3,
                column=0,
                sticky="w",
                padx=12,
                pady=(1, 4),
            )

            self._text(
                age_row,
                "Messwertalter:",
                "body",
                height=18,
            ).grid(
                row=0,
                column=0,
                sticky="w",
                padx=(0, 8),
            )

            age = self._text(
                age_row,
                "—",
                "strong",
                height=20,
                corner_radius=5,
                fg_color="#CEE7FB",
            )
            age.grid(
                row=0, column=1, sticky="w", ipadx=6
            )
            self.age_labels[name] = age

        # Rechte Spalte: zusammenhängende Tabelle statt drei großer Karten.
        panel = self._panel(upper)
        panel.grid(row=0, column=1, sticky="nsew")
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(1, weight=1)

        self._text(
            panel,
            "Manuelle Stellbefehle",
            "heading",
            height=20,
        ).grid(
            row=0,
            column=0,
            sticky="w",
            padx=12,
            pady=(4, 2),
        )

        table = ctk.CTkFrame(
            panel,
            fg_color="#D4DFEA",
            corner_radius=6,
            border_width=1,
            border_color="#BDCCDC",
        )
        table.grid(
            row=1, column=0, sticky="new", padx=10
        )
        table.grid_columnconfigure(0, weight=1)
        self._command_table = table

        self._spin_buttons = {}
        self._spin_frames = {}
        self._table_rows = []

        # Zeile 0 enthält die Spaltenüberschriften.
        # Zeilen 1–3 enthalten FU, GLF und GHF.
        for row in range(4):
            band = ctk.CTkFrame(
                table,
                corner_radius=0,
                fg_color=(
                    "#DCE6EF" if row == 0 else
                    "#F5F8FC" if row % 2 else
                    "#EDF3F9"
                ),
            )
            band.grid(
                row=row,
                column=0,
                sticky="nsew",
                padx=1,
                pady=(1, 0) if row < 3 else (1, 1),
            )
            band.grid_rowconfigure(0, weight=1)

            # Identische Spaltenproportionen in Kopf- und Datenzeilen.
            for col, weight in enumerate((22, 40, 38)):
                band.grid_columnconfigure(
                    col,
                    weight=weight,
                    uniform="columns",
                )

            if row == 0:
                for col, title in enumerate((
                    "Kanal",
                    "Sollwert [%]",
                    "Letzter bestätigter Befehl",
                )):
                    self._text(
                        band,
                        title,
                        "table_header",
                        height=18,
                    ).grid(
                        row=0,
                        column=col,
                        sticky="w",
                        padx=10,
                    )
                continue

            name = ("FU", "GLF", "GHF")[row - 1]
            self._table_rows.append(band)

            self._text(
                band,
                name,
                "channel",
                height=20,
            ).grid(
                row=0,
                column=0,
                sticky="w",
                padx=10,
                pady=1,
            )

            field = ctk.CTkFrame(
                band,
                fg_color="transparent",
            )
            field.grid(
                row=0,
                column=1,
                sticky="w",
                padx=10,
                pady=1,
            )

            # Ein leeres Feld bedeutet weiterhin:
            # Diesen Kanal beim Übernehmen unverändert lassen.
            variable = ctk.StringVar(
                master=self,
                value="",
            )
            self.entry_variables[name] = variable

            # Feste Eingabebreite: Das Zahlenfeld wird nicht mehr
            # über die gesamte verfügbare Tabellenspalte gestreckt.
            spin = ctk.CTkFrame(
                field,
                width=132,
                height=30,
                corner_radius=5,
                fg_color="#FBFCFE",
                border_width=1,
                border_color="#8D9DB0",
            )
            spin.grid(row=0, column=0, sticky="w")
            spin.grid_propagate(False)
            spin.grid_columnconfigure(0, weight=1)
            spin.grid_rowconfigure((0, 1), weight=1)

            # self.inputs enthält weiterhin echte CTkEntry-Widgets,
            # damit die vorhandenen Bedienfunktionen weiterarbeiten.
            entry = ctk.CTkEntry(
                spin,
                width=100,
                height=28,
                textvariable=variable,
                font=self._fonts["entry"],
                border_width=0,
                fg_color="#FBFCFE",
                text_color=COLOR_DARK,
                corner_radius=4,
            )
            entry.grid(
                row=0,
                column=0,
                rowspan=2,
                sticky="ew",
                padx=(3, 0),
            )

            self.inputs[name] = entry
            self._spin_frames[name] = spin

            buttons = []

            # Beide Pfeile ändern ausschließlich den Eingabetext.
            # Sie lösen selbst keinen Schreibvorgang aus.
            for arrow_row, (symbol, step) in enumerate(
                (("▴", 1), ("▾", -1))
            ):
                button = ctk.CTkButton(
                    spin,
                    text=symbol,
                    width=20,
                    height=12,
                    corner_radius=2,
                    border_width=0,
                    font=self._fonts["small"],
                    fg_color="#E7EEF5",
                    hover_color="#D2E1ED",
                    text_color=COLOR_DARK,
                    command=lambda channel=name, delta=step:
                        self._step_setpoint(channel, delta),
                )
                button.grid(
                    row=arrow_row,
                    column=1,
                    sticky="nsew",
                    padx=(1, 2),
                    pady=(2, 0) if arrow_row == 0 else (0, 2),
                )
                buttons.append(button)

            self._spin_buttons[name] = buttons

            self._text(
                field,
                "%",
                "entry",
                height=26,
            ).grid(
                row=0,
                column=1,
                sticky="w",
                padx=(8, 0),
            )

            command_label = self._text(
                band,
                "—",
                "command",
                height=20,
            )
            command_label.grid(
                row=0,
                column=2,
                sticky="w",
                padx=10,
            )
            self.command_labels[name] = command_label

        self.fu_variable = self.entry_variables["FU"]
        self.fu_variable.trace_add(
            "write",
            lambda *_: self._update_preview(),
        )

        self._text(
            panel,
            "Befehlswerte · keine Ausgangsrückmessung",
            "small",
            height=12,
        ).grid(
            row=2,
            column=0,
            sticky="w",
            padx=12,
            pady=(2, 0),
        )

        actions = ctk.CTkFrame(
            panel,
            fg_color="transparent",
        )
        actions.grid(
            row=3,
            column=0,
            sticky="ew",
            padx=10,
            pady=2,
        )
        actions.grid_columnconfigure(
            (0, 1),
            weight=1,
            uniform="actions",
        )

        self.output_button = ctk.CTkButton(
            actions,
            text="▶  Sollwerte übernehmen",
            height=30,
            font=self._fonts["button"],
            fg_color="#008C83",
            hover_color="#00766E",
            state="disabled",
            command=self.output_update,
        )
        self.output_button.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=(0, 4),
        )

        self.clear_button = ctk.CTkButton(
            actions,
            text="Eingaben leeren",
            height=30,
            font=self._fonts["button"],
            fg_color="#F9FBFE",
            border_width=1,
            border_color="#8799AD",
            hover_color="#E4EDF6",
            text_color=COLOR_DARK,
            command=self._clear_inputs,
        )
        self.clear_button.grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(4, 0),
        )

        # Bestätigungen und Fehlermeldungen bleiben auf der Hauptseite.
        self.output_status = self._text(
            panel,
            "Noch kein Stellbefehl gesendet",
            "small",
            height=12,
            anchor="w",
            wraplength=470,
        )
        self.output_status.grid(
            row=4,
            column=0,
            sticky="ew",
            padx=12,
            pady=(0, 2),
        )

        # Unterer Bereich: zwei gleich große Diagramme.
        plots = self._panel(tab)
        plots.grid(row=1, column=0, sticky="nsew")
        plots.grid_columnconfigure(
            (0, 1),
            weight=1,
            uniform="plots",
        )
        plots.grid_rowconfigure(1, weight=1)

        self._text(
            plots,
            "MESSWERTVERLÄUFE · " +
            ("SIMULATION" if self.simulate else "HARDWARE"),
            "table_header",
            height=18,
        ).grid(
            row=0,
            column=0,
            columnspan=2,
            sticky="w",
            padx=10,
            pady=(3, 0),
        )

        for column, name in enumerate(("GHF", "GLF")):
            frame = self._panel(plots)
            frame.grid(
                row=1,
                column=column,
                sticky="nsew",
                padx=(4, 3) if column == 0 else (3, 4),
                pady=(0, 4),
            )
            frame.grid_columnconfigure(0, weight=1)
            frame.grid_rowconfigure(1, weight=1)

            self._text(
                frame,
                f"{name} · Durchflussverlauf",
                "heading",
                height=22,
            ).grid(
                row=0,
                column=0,
                sticky="w",
                padx=10,
                pady=(3, 0),
            )

            canvas = tk.Canvas(
                frame,
                background="#F6F9FD",
                highlightthickness=0,
                width=100,
                height=40,
            )
            canvas.grid(
                row=1,
                column=0,
                sticky="nsew",
                padx=4,
                pady=(0, 4),
            )
            canvas.bind(
                "<Configure>",
                self._mark_graphs_dirty,
                add="+",
            )
            self.graphs[name] = canvas

    def _step_setpoint(self, name, delta):
        """Ändert den Sollwertentwurf um einen Prozentpunkt."""
        entry = self.inputs[name]

        # Gesperrte Eingaben dürfen auch über die Pfeile nicht geändert werden.
        if entry.cget("state") == "disabled":
            return

        try:
            current = parse_percent(entry.get(), name)
        except ValueError:
            # Fehlerhafte Texteingaben bleiben zur Korrektur erhalten.
            return

        value = max(
            0.0,
            min(
                100.0,
                (current if current is not None else 0.0) + delta,
            ),
        )

        self.entry_variables[name].set(
            number_de(value, 0 if value.is_integer() else 2)
        )

    def _refresh_controls(self):
        # Die vorhandenen Messwert-, Fehler- und Abschlussprüfungen
        # entscheiden weiterhin über die Bedienfreigabe.
        super()._refresh_controls()

        # Die zusätzlichen Pfeiltasten erhalten denselben Zustand
        # wie das zugehörige Eingabefeld.
        for name, buttons in self._spin_buttons.items():
            state = self.inputs[name].cget("state")
            for button in buttons:
                self._configure_if_changed(
                    button,
                    state=state,
                )

        if self.output_button.cget("state") == "normal":
            self._configure_if_changed(
                self.output_button,
                fg_color="#008C83",
            )

    def _configure_if_changed(self, widget, **options):
        # Die Basisklasse liefert "Sensorstrom: ...".
        # Die neue Karte besitzt dafür eine separate feste Beschriftung.
        if widget in getattr(self, "current_labels", {}).values():
            text = options.get("text")
            if text is not None and text.startswith("Sensorstrom: "):
                options["text"] = text.removeprefix("Sensorstrom: ")

            if options.get("text_color") == TEXT_MUTED:
                options["text_color"] = COLOR_DARK

        super()._configure_if_changed(widget, **options)

    def _build_settings_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)

        # Nur die Informationsseite darf bei Bedarf scrollen.
        body = ctk.CTkScrollableFrame(
            tab,
            fg_color=CARD_BG,
        )
        body.grid(row=0, column=0, sticky="nsew")

        self._label(
            body,
            "Einstellungen und Systeminformationen",
            size=20,
            bold=True,
        ).pack(
            anchor="w",
            padx=16,
            pady=(12, 6),
        )

        for name, channel in [
            *AO_CHANNELS.items(),
            ("GHF-Sensor", AI_GHF),
            ("GLF-Sensor", AI_GLF),
        ]:
            self._label(
                body,
                f"{name}: {channel}",
                anchor="w",
            ).pack(
                fill="x",
                padx=16,
            )

        self.preview_label = self._label(
            body,
            "FU-Stromvorschau: — mA",
            anchor="w",
        )
        self.preview_label.pack(
            fill="x",
            padx=16,
            pady=(6, 0),
        )

        self._label(
            body,
            f"Software-Plausibilität: "
            f"{number_de(SENSOR_VALID_MIN_A * 1000, 1)}–"
            f"{number_de(SENSOR_VALID_MAX_A * 1000, 1)} mA\n"
            "FU: 0 % = 4 mA, 100 % = 20 mA\n"
            "GLF/GHF: 0 % = 20 mA, 100 % = 4 mA\n"
            "Messwertalter = Zeit seit Softwareempfang, "
            "kein Hardware-Zeitstempel.\n"
            "Verläufe enthalten angezeigte Messpakete, "
            "keine zusätzlichen Abfragen.",
            justify="left",
            anchor="w",
            wraplength=620,
        ).pack(
            fill="x",
            padx=16,
            pady=8,
        )

        # Ausführliche Sensorzustände bleiben hier verfügbar.
        for name in ("GHF", "GLF"):
            label = self._label(
                body,
                f"{name}: Warte auf Messdaten",
                anchor="w",
                wraplength=620,
            )
            label.pack(fill="x", padx=16)
            self.quality_labels[name] = label

        self.measurement_status = self._label(
            body,
            "Initialisierung",
            anchor="w",
            wraplength=620,
        )
        self.measurement_status.pack(fill="x", padx=16)

        self.task_label = self._label(
            body,
            "Tasks: noch nicht initialisiert",
            anchor="w",
            justify="left",
            wraplength=620,
        )
        self.task_label.pack(
            fill="x",
            padx=16,
            pady=6,
        )

        self._label(
            body,
            "Ereignisprotokoll",
            size=16,
            bold=True,
        ).pack(
            anchor="w",
            padx=16,
            pady=(10, 4),
        )

        self.log_box = ctk.CTkTextbox(
            body,
            height=200,
            fg_color="#F2F5F7",
            text_color=COLOR_DARK,
            font=ctk.CTkFont(
                family="Consolas",
                size=12,
            ),
        )
        self.log_box.pack(
            fill="x",
            padx=16,
            pady=(0, 12),
        )
        self.log_box.configure(state="disabled")

    def _fit_dashboard(self, event=None, force=False):
        """Passt Schriften und Bedienelemente an die Fenstergröße an."""
        if event is not None and event.widget is not self:
            return

        if self.closing or self.shutdown_blocked:
            return

        dimensions = (
            self.geometry()
            .split("+", 1)[0]
            .split("-", 1)[0]
        )
        width, height = map(int, dimensions.split("x", 1))
        compact = height < 650 or width < 1100

        if compact == self._density and not force:
            return

        self._density = compact

        sizes = {
            "title": 16 if compact else 19,
            "heading": 13 if compact else 21,
            "value": 24 if compact else 46,
            "unit": 22 if compact else 40,
            "body": 10 if compact else 15,
            "strong": 11 if compact else 17,
            "channel": 15 if compact else 24,
            "entry": 16 if compact else 24,
            "command": 12 if compact else 20,
            "table_header": 10 if compact else 14,
            "small": 9 if compact else 10,
            "button": 11 if compact else 16,
        }

        for role, size in sizes.items():
            self._fonts[role].configure(size=size)

        self._axis_font.configure(
            size=-10 if compact else -14
        )
        self._badge_font.configure(
            size=-11 if compact else -16
        )

        # Im kompakten Fenster bleiben die Tabellenzeilen fest.
        # Im großen Fenster füllen sie den begrenzten Tabellenbereich.
        self._command_table.grid(
            sticky="new" if compact else "nsew"
        )

        for row in (1, 2, 3):
            self._command_table.grid_rowconfigure(
                row,
                weight=0 if compact else 1,
                uniform="data" if not compact else "",
            )

        row_height = 26 if compact else 46
        for band in self._table_rows:
            band.grid_rowconfigure(
                0,
                minsize=row_height,
            )

        for name, entry in self.inputs.items():
            entry.configure(
                height=24 if compact else 34
            )
            self._spin_frames[name].configure(
                width=110 if compact else 172,
                height=26 if compact else 36,
            )

        for button in (
            self.output_button,
            self.clear_button,
            self.close_button,
            self.reconnect_button,
        ):
            button.configure(
                height=26 if compact else 40,
                font=self._fonts["button"],
            )

        for button in self.nav_buttons.values():
            button.configure(
                width=88 if compact else 104,
                height=28,
            )

        # Bei kleinen Fenstern etwas mehr Höhe für die Bedienung reservieren.
        self._control_page.grid_rowconfigure(
            0,
            weight=3 if compact else 11,
        )
        self._control_page.grid_rowconfigure(
            1,
            weight=2 if compact else 9,
        )

        self.output_status.configure(
            wraplength=470 if compact else 760
        )
        self._graphs_dirty = True

    def _show_written(self, commands):
        commands = tuple(commands)

        # Weiterhin nur tatsächlich bestätigte Teilbefehle übernehmen
        # und deren Eingabefelder anschließend leeren.
        super()._show_written(commands)

        for name, percent, current in commands:
            self.command_labels[name].configure(
                text=number_de(current * 1000, 3) + " mA",
                text_color=COLOR_DARK,
            )

    def _mark_graphs_dirty(self, _event=None):
        self._graphs_dirty = True

    def _render_measurement(self):
        # Bestehende Einzelkanalbewertung und Ausgabefreigaben ausführen.
        super()._render_measurement()

        now = time.monotonic()

        for name, convert in (
            ("GHF", ghf_amp_to_m3h),
            ("GLF", glf_amp_to_m3h),
        ):
            reading = self.sensor_readings[name]

            # Das Alter gehört zum angezeigten Wert, nicht zu einer Fehlermeldung.
            displayed_at = self._display_times.get(name, 0.0)
            age = max(0, round((now - displayed_at) * 1000)) if displayed_at else None
            cached = self._display_cached.get(name, False)
            text = "—" if age is None else f"{age} ms"
            color = COLOR_DARK

            if self.measurement_error or (reading.received_at and not reading.valid):
                color = COLOR_RED
            elif cached or self.closing or self.shutdown_blocked:
                color = COLOR_ORANGE

            if cached:
                text += " · ALT"
            elif color == COLOR_RED:
                text += " · FEHLER"

            self._configure_if_changed(
                self.age_labels[name], text=text, text_color=color)

            # Pro neuem Softwareempfang höchstens einen Punkt speichern.
            # Ein ungültiger Messwert erzeugt eine Lücke im Verlauf.
            if (
                reading.received_at
                and reading.received_at != self._recorded[name]
            ):
                self._recorded[name] = reading.received_at

                valid = (
                    reading.valid
                    and not self.measurement_error
                    and reading.recent(now)
                )
                value = (
                    convert(reading.current_a)
                    if valid else None
                )

                self._history[name].append(
                    (reading.received_at, value)
                )

            # Das Diagramm zeigt ausschließlich die letzten 60 Sekunden.
            while (
                self._history[name]
                and self._history[name][0][0] < now - 60
            ):
                self._history[name].popleft()

        # Den vorhandenen GUI-Poller verwenden.
        # Kein zusätzlicher Thread und keine weitere after()-Schleife.
        if (
            self._graphs_dirty
            or now - self._plot_at >= max(
                0.2,
                GUI_REFRESH_MS / 1000,
            )
        ):
            self._graphs_dirty = False
            self._plot_at = now

            for name in ("GHF", "GLF"):
                self._draw_graph(name, now)

    def _draw_graph(self, name, now):
        canvas = self.graphs[name]

        if not canvas.winfo_ismapped():
            return

        width = canvas.winfo_width()
        height = canvas.winfo_height()

        if width < 60 or height < 20:
            return

        canvas.delete("all")

        history = list(self._history[name])
        valid_values = [
            value
            for _, value in history
            if value is not None
        ]

        if not valid_values:
            canvas.create_text(
                width / 2,
                height / 2,
                text="Noch keine gültigen Messdaten",
                fill=TEXT_MUTED,
                font=self._axis_font,
            )
            return

        # Jeder Kanal erhält eine eigene Skala.
        # Vorhandene kalibrierte Werte werden nicht verändert oder geklemmt.
        low = min(valid_values)
        high = max(valid_values)
        padding = max(1.0, (high - low) * 0.15)
        low -= padding
        high += padding

        line_height = self._axis_font.metrics("linespace")
        mini = height < line_height * 5

        left = (
            6 if mini
            else self._axis_font.measure("-999,9") + 10
        )
        right = width - 10
        top = 6 if mini else line_height + 4
        bottom = height - (
            6 if mini else line_height * 2 + 8
        )

        if bottom <= top or right <= left:
            return

        def point(timestamp, value):
            x = left + (
                (timestamp - (now - 60)) / 60
                * (right - left)
            )
            y = bottom - (
                (value - low) / (high - low)
                * (bottom - top)
            )
            return x, y

        if not mini:
            # Horizontales Raster und Durchflussskala.
            for index in range(4):
                value = low + (high - low) * index / 3
                y = point(now, value)[1]

                canvas.create_line(
                    left, y, right, y,
                    fill="#DFE7EE",
                )
                canvas.create_text(
                    left - 6,
                    y,
                    text=number_de(value, 1),
                    anchor="e",
                    font=self._axis_font,
                    fill=TEXT_MUTED,
                )

            # Gemeinsame Zeitachse: -60 Sekunden bis jetzt.
            for seconds in (-60, -45, -30, -15, 0):
                x = left + (
                    (seconds + 60) / 60
                    * (right - left)
                )

                canvas.create_line(
                    x, top, x, bottom,
                    fill="#E8EDF2",
                )
                canvas.create_text(
                    x,
                    bottom + 3,
                    text=str(seconds),
                    anchor="n",
                    font=self._axis_font,
                    fill=TEXT_MUTED,
                )

            canvas.create_text(
                left,
                0,
                text="m³/h",
                anchor="nw",
                font=self._axis_font,
                fill=TEXT_MUTED,
            )
            canvas.create_text(
                (left + right) / 2,
                height - 1,
                text="Zeit [s]",
                anchor="s",
                font=self._axis_font,
                fill=TEXT_MUTED,
            )

        color = (
            "#009688" if name == "GHF"
            else "#176FD1"
        )

        segment = []
        previous_time = None

        def draw_segment():
            if len(segment) >= 4:
                canvas.create_line(
                    *segment,
                    fill=color,
                    width=2,
                    smooth=False,
                )

        # Fehlerhafte Messungen und längere Empfangspausen dürfen
        # nicht durch eine scheinbar durchgängige Linie verbunden werden.
        for timestamp, value in history:
            gap = (
                previous_time is not None
                and timestamp - previous_time > MAX_MEASUREMENT_AGE_S
            )

            if value is None or gap:
                draw_segment()
                segment = []

            if value is not None:
                segment.extend(point(timestamp, value))

            previous_time = timestamp

        draw_segment()

        reading = self.sensor_readings[name]

        # Ein Live-Wertkästchen erscheint ausschließlich bei einem
        # aktuell gültigen Kanal ohne Messfehler oder aktiven Abschluss.
        live = (
            not (
                self.measurement_error
                or self.closing
                or self.shutdown_blocked
            )
            and reading.valid
            and reading.recent(now)
            and history
            and history[-1][1] is not None
            and now - history[-1][0] <= MAX_MEASUREMENT_AGE_S
        )

        if live:
            x, y = point(*history[-1])

            canvas.create_oval(
                x - 3,
                y - 3,
                x + 3,
                y + 3,
                fill=color,
                outline=CARD_BG,
            )

            # Tatsächlichen letzten Messwert anzeigen.
            text = (
                number_de(history[-1][1], 2)
                + " m³/h"
            )
            badge_width = self._badge_font.measure(text) + 16
            badge_height = (
                self._badge_font.metrics("linespace") + 8
            )

            # Das Kästchen muss vollständig in den Canvas passen.
            if (
                badge_width < right - left
                and badge_height < bottom - top
            ):
                bx = max(
                    left,
                    min(
                        x - badge_width + 6,
                        right - badge_width,
                    ),
                )
                by = max(
                    2,
                    min(
                        y - badge_height - 10,
                        bottom - badge_height,
                    ),
                )

                # Wenn oberhalb nicht genug Platz vorhanden ist,
                # das Kästchen unterhalb des Punktes platzieren.
                if by + badge_height > y - 5:
                    by = min(
                        bottom - badge_height,
                        y + 10,
                    )

                canvas.create_rectangle(
                    bx,
                    by,
                    bx + badge_width,
                    by + badge_height,
                    fill=(
                        "#D9F5EE" if name == "GHF"
                        else "#DEEDFF"
                    ),
                    outline=(
                        "#8DDACC" if name == "GHF"
                        else "#9DC5F7"
                    ),
                )
                canvas.create_text(
                    bx + badge_width / 2,
                    by + badge_height / 2,
                    text=text,
                    font=self._badge_font,
                    fill=COLOR_DARK,
                )
