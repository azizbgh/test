# -*- coding: utf-8 -*-
"""Nassheuer | Luftsteuerung — eigenstaendige TESTVERSION.

START: Diese Datei unter Windows mit Python oeffnen oder ausfuehren:
    python luftsteuerung_testversion.py

Nur die Python-Standardbibliothek wird fuer die Simulation benoetigt.
    python luftsteuerung_testversion.py --self-test
    python luftsteuerung_testversion.py --hardware-readonly

WICHTIGE EINORDNUNG
------------------
Diese Version ist ein neu aufgebauter Teststand. Der letzte hochgeladene
Programmstand war beim Erstellen nicht erneut abrufbar; deshalb wird keine
vollstaendige Uebernahme dieses unbekannten Standes behauptet.

Die GUI bietet Steuerung, Regelung und Einstellungen. Stellbefehle wirken
AUSSCHLIESSLICH auf ein kuenstliches Simulationsmodell. Das Modell verwendet
bewusst vereinfachte, erfundene Dynamik und KEINE vermessene Anlagenkennlinie.
Sein Verhalten beweist weder Stabilitaet noch Erreichbarkeit an der Anlage.

--hardware-readonly liest optional zwei NI-Stromsensoren. Es gibt in dieser
Datei KEINEN NI-Analogausgangstask und KEINEN NI-Ausgangsschreibaufruf. Auch
beim Start, Fehler oder Schliessen werden keine realen Ausgaenge beschrieben.
Damit ist diese Testversion kein betriebsbereiter Ersatz fuer LabVIEW.

FESTLEGUNGEN FUER DEN TESTSTAND
-----------------------------
Gemeinsamer gewuenschter Sollbereich 92-549 m3/h, vorlaeufige Toleranz +/-20.
GHF-Grundlast und GLF-Feinkorrektur; alternativ nur einer der beiden Zweige.
Aktive simulierte Ventil-PIDs: 20-100 %. Nicht genutzter Zweig: 0 %.
Bewusster Regler-Stopp haelt die letzten Simulationsbefehle.
Regelfehler setzt im Modell FU auf 0 %, GLF/GHF auf 100 %: dreimal 4 mA.
Das ist der gewuenschte Ruecksetzzustand und kein Nachweis eines Stillstands.
GLF fuer 10 Sekunden unter 5 m3/h bei Verwendung: Meldung und Modell-Reset.
GHF Kc=.900, Ti=.108 min, Td=.007 min; GLF .320/.075/.001 min.
Eigene, dokumentierte PID-Rechnung; keine nachgewiesene LabVIEW-Identitaet.

Bedienhinweise und die offenen Originaldetails stehen auf Einstellungen.
Alle Zeitmessungen benutzen time.monotonic(), nicht die Wanduhr.
"""
from __future__ import annotations



# Reine TESTLOGIK. Diese Datei enthält keinerlei NI-/DAQ-/AO-Zugriff.
# Das Pflanzenmodell ist synthetisch und keine identifizierte Anlagenkennlinie.
from dataclasses import dataclass
import math
import time

GHF_SENSOR_FACTOR = 0.0000356
GLF_SENSOR_FACTOR = 0.00016
CURRENT_MIN_A = 0.0039
CURRENT_MAX_A = 0.0201
FAULT_COMMANDS = {"FU": 0.0, "GHF": 100.0, "GLF": 100.0}


def _finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name}: endliche Zahl erforderlich.")
    return float(value)


def _clip(value, low, high):
    return min(high, max(low, value))


def percent_to_current(name, percent):
    """Elektrische Sollzuordnung zur ANZEIGE; schreibt keinen Ausgang."""
    percent = _finite(percent, name)
    if name not in ("FU", "GHF", "GLF") or not 0 <= percent <= 100:
        raise ValueError("Unbekannter Kanal oder Stellwert außerhalb 0…100 %.")
    return .004 + .016 * percent / 100 if name == "FU" else .020 - .016 * percent / 100


@dataclass(frozen=True)
class FlowSample:
    received_at: float
    ghf_current_a: float | None
    glf_current_a: float | None

    def currents(self):
        return {"GHF": self.ghf_current_a, "GLF": self.glf_current_a}

    def flows(self):
        result = {}
        for name, factor in (("GHF", GHF_SENSOR_FACTOR), ("GLF", GLF_SENSOR_FACTOR)):
            raw = self.currents()[name]
            value = (raw - .004) / factor if isinstance(raw, (int, float)) and not isinstance(raw, bool) and math.isfinite(raw) else None
            result[name] = value if value is not None and math.isfinite(value) else None
        return result

    def valid(self, name, now, max_age=1.0):
        raw = self.currents().get(name)
        return (
            isinstance(raw, (int, float)) and not isinstance(raw, bool)
            and math.isfinite(raw) and CURRENT_MIN_A <= raw <= CURRENT_MAX_A
            and isinstance(self.received_at, (int, float)) and not isinstance(self.received_at, bool)
            and math.isfinite(self.received_at)
            and isinstance(now, (int, float)) and not isinstance(now, bool)
            and math.isfinite(now) and 0 <= now - self.received_at <= max_age
        )


class SimulatedPlant:
    """Anschauliches, synthetisches Modell zweier Zweige und eines Lüfters.

    Kennlinien, Zeitkonstanten und Druckkopplung sind DEMOWERTE. Bei allen
    Schiebern offen fördert das Modell 92…549 m³/h abhängig von FU. Daraus
    folgt ausdrücklich keine erreichbare Menge oder Dynamik der echten Anlage.
    """
    def __init__(self, now=None):
        self.time = time.monotonic() if now is None else _finite(now, "Modellzeit")
        self.magnet_open = True
        self._commands = dict(FAULT_COMMANDS)
        self.ghf = 92.0 * 449.0 / 549.0
        self.glf = 92.0 * 100.0 / 549.0

    @property
    def commands(self):
        return dict(self._commands)

    def sample(self):
        return FlowSample(self.time, .004 + self.ghf * GHF_SENSOR_FACTOR,
                          .004 + self.glf * GLF_SENSOR_FACTOR)

    def step(self, dt, commands):
        dt = _finite(dt, "Modell-Zeitschritt")
        if not 0 < dt <= 60:
            raise ValueError("Modell-Zeitschritt muss >0 und höchstens 60 s sein.")
        checked = dict(self._commands)
        for name, value in commands.items():
            percent_to_current(name, value)
            checked[name] = float(value)
        self._commands = checked
        g = checked["GHF"] / 100
        l = checked["GLF"] / 100 if self.magnet_open else 0.0
        load = (449.0 * g + 100.0 * l) / 549.0
        fan = 92.0 / 549.0 + (1.0 - 92.0 / 549.0) * checked["FU"] / 100
        coupling = 1.0 + .30 * (1.0 - load)
        goal_g = _clip(449.0 * g * fan * coupling, 0.0, 449.0)
        goal_l = _clip(100.0 * l * fan * coupling, 0.0, 100.0)
        self.ghf += (goal_g - self.ghf) * (1.0 - math.exp(-dt / 2.0))
        self.glf += (goal_l - self.glf) * (1.0 - math.exp(-dt / 1.0))
        self.time += dt
        return self.sample()


@dataclass(frozen=True)
class RegulatorConfig:
    mode: str = "BOTH"
    target: float = 300.0
    # Übernommene Werte; dies ist eine eigene PID-Implementierung, kein
    # Nachweis identischen Verhaltens eines unbekannten LabVIEW-PID-VI.
    ghf_kc: float = .900
    ghf_ti_s: float = 6.48
    ghf_td_s: float = .42
    glf_kc: float = .320
    glf_ti_s: float = 4.50
    glf_td_s: float = .06
    fu_factor: float = 1.05  # Nur für die ausdrücklich synthetische SIM-Vorsteuerung.
    derivative_filter_s: float = .20
    preferred_low: float = 40.0
    preferred_high: float = 60.0
    rebalance_delay_s: float = 2.0
    base_rate_m3h_s: float = 2.0
    valve_rate_percent_s: float = 5.0
    fu_rate_percent_s: float = 3.0
    max_age_s: float = 1.0
    max_gap_s: float = 1.0
    glf_low_delay_s: float = 10.0

    def validate(self):
        if self.mode not in ("GLF", "GHF", "BOTH"):
            raise ValueError("Betriebsart muss GLF, GHF oder BOTH sein.")
        for name, value in vars(self).items():
            if name != "mode":
                _finite(value, name)
        low, high = {"GLF": (10., 100.), "GHF": (50., 449.), "BOTH": (92., 549.)}[self.mode]
        if not low <= self.target <= high:
            raise ValueError(f"Sollwert für {self.mode}: {low:g}…{high:g} m³/h.")
        if not 10 < self.preferred_low < self.preferred_high < 100:
            raise ValueError("GLF-Arbeitsband muss innerhalb 10…100 m³/h liegen.")
        if min(self.ghf_kc, self.ghf_ti_s, self.glf_kc, self.glf_ti_s,
               self.fu_factor, self.derivative_filter_s, self.base_rate_m3h_s, self.valve_rate_percent_s,
               self.fu_rate_percent_s, self.max_age_s, self.max_gap_s, self.glf_low_delay_s) <= 0:
            raise ValueError("Verstärkungen, Zeiten und Stellraten müssen positiv sein.")
        if min(self.ghf_td_s, self.glf_td_s, self.rebalance_delay_s) < 0:
            raise ValueError("Vorhalte- und Beruhigungszeiten dürfen nicht negativ sein.")


class PID:
    """Kc*(e + Integral(e)/Ti − Td*d(PV)/dt), gefiltertes D auf PV.

    Reale Zeitdifferenz, Rampen-/Ausgangsbegrenzung und Rückführung des
    Integrators auf den begrenzten Wert. Kein automatischer Hardwarezugriff.
    """
    def __init__(self, kc, ti_s, td_s, filter_s=.2, rate=5., low=20., high=100.):
        for name, value in (("Kc", kc), ("Ti", ti_s), ("Td", td_s), ("Filter", filter_s), ("Rate", rate), ("Minimum", low), ("Maximum", high)):
            _finite(value, name)
        if min(kc, ti_s, filter_s, rate) <= 0 or td_s < 0 or not 0 <= low < high <= 100:
            raise ValueError("Ungültige PID-Parameter.")
        self.kc, self.ti, self.td = kc, ti_s, td_s
        self.filter_s, self.rate, self.low, self.high = filter_s, rate, low, high
        self.integral = self.output = self.derivative = 0.0
        self.previous = None

    def reset(self, output, target, pv):
        self.output = _clip(_finite(output, "Übernahmewert"), self.low, self.high)
        self.integral = self.output - self.kc * (_finite(target, "Sollwert") - _finite(pv, "Istwert"))
        _finite(self.integral, "PID-Integral")
        self.previous, self.derivative = pv, 0.0

    def update(self, target, pv, dt):
        target, pv, dt = _finite(target, "Sollwert"), _finite(pv, "Istwert"), _finite(dt, "dt")
        if dt <= 0:
            raise ValueError("PID benötigt positives dt.")
        error = target - pv
        slope = 0.0 if self.previous is None else (pv - self.previous) / dt
        self.derivative += dt / (self.filter_s + dt) * (slope - self.derivative)
        p = self.kc * error
        d = -self.kc * self.td * self.derivative
        candidate_i = self.integral + self.kc / self.ti * error * dt
        raw = p + candidate_i + d
        for value in (slope, self.derivative, p, d, candidate_i, raw):
            _finite(value, "PID-Zwischenwert")
        result = _clip(raw, max(self.low, self.output - self.rate * dt), min(self.high, self.output + self.rate * dt))
        self.integral = result - p - d if result != raw else candidate_i
        self.output, self.previous = result, pv
        return result


class AirflowRegulator:
    """Nur Simulation: GHF-Grundlast, GLF-Gesamtfehler, synthetische FU-Vorsteuerung."""
    def __init__(self):
        self.config = None
        self.active = False
        self.state, self.reason = "idle", "Regelung nicht gestartet"
        self._commands = dict(FAULT_COMMANDS)
        self._snapshot = {}
        self.base = None
        self._last_time = self._last_packet = None
        self._low_start = self._low_last = None
        self._low_seconds = 0.0
        self._direction = 0
        self._direction_since = None
        self._need_reset = True

    @property
    def commands(self):
        return dict(self._commands)

    @property
    def snapshot(self):
        return dict(self._snapshot)

    def start(self, config, sample, now, commands=None):
        config.validate()
        now = _finite(now, "Zeit")
        required = ("GHF", "GLF") if config.mode == "BOTH" else (config.mode,)
        for name in required:
            if not sample.valid(name, now, config.max_age_s):
                raise ValueError(f"{name}: aktueller plausibler Sensorstrom erforderlich.")
        checked = dict(self._commands if commands is None else commands)
        for name in ("FU", "GHF", "GLF"):
            if name not in checked:
                raise ValueError(f"{name}: bisheriger Stellbefehl fehlt.")
            percent_to_current(name, checked[name])
        self.config, self._commands = config, checked
        self.base = _clip(sample.flows()["GHF"], max(50., config.target - 100.), min(449., config.target - 10.)) if config.mode == "BOTH" else None
        self._g_pid = PID(config.ghf_kc, config.ghf_ti_s, config.ghf_td_s, config.derivative_filter_s, config.valve_rate_percent_s)
        self._l_pid = PID(config.glf_kc, config.glf_ti_s, config.glf_td_s, config.derivative_filter_s, config.valve_rate_percent_s)
        self.active, self.state, self.reason = True, "running", "Simulationsregelung aktiv"
        self._last_time, self._last_packet = now, None
        self._low_start = self._low_last = None
        self._low_seconds = 0.0
        self._direction, self._direction_since = 0, None
        self._need_reset = True
        self._fu_goal = self._simulation_feedforward(config)
        return self._make_snapshot(sample)

    @staticmethod
    def _simulation_feedforward(config):
        # Ausschließlich zur oben definierten synthetischen Modellkapazität.
        # Die Zahl 1,05 wird nicht als unbekannte Hardware-Kennlinie eingesetzt.
        capacity = {"BOTH": 549., "GHF": 449., "GLF": 100.}[config.mode]
        fraction = min(1.0, config.target / capacity * config.fu_factor)
        return _clip((fraction - 92. / 549.) / (1. - 92. / 549.) * 100., 0., 100.)

    def retarget(self, target, now=None):
        if not self.active:
            raise ValueError("Regelung zuerst starten.")
        values = vars(self.config).copy()
        values["target"] = target
        new = RegulatorConfig(**values)
        new.validate()
        self.config = new
        if new.mode != "BOTH":
            self._fu_goal = self._simulation_feedforward(new)
        # Kleine BOTH-Änderungen bleiben bei GLF; FU/Basis werden nicht neu zugeteilt.
        self._direction, self._direction_since = 0, None
        self._snapshot["target"] = target
        return self.snapshot

    def stop(self, reason="Regelung angehalten · letzte Stellbefehle bleiben bestehen"):
        self.active = False
        self.state, self.reason = "paused", reason
        self._snapshot.update(active=False, state=self.state, reason=reason)
        return self.snapshot

    def fault(self, reason):
        self.active = False
        self.state, self.reason = "error", str(reason)
        # Ausschließlich Simulationsbefehle; keine einzige Hardware-AO-Funktion.
        self._commands = dict(FAULT_COMMANDS)
        self._snapshot.update(active=False, state=self.state, reason=self.reason,
                              fu_percent=0., ghf_percent=100., glf_percent=100.)
        return self.snapshot

    def _make_snapshot(self, sample):
        flows = sample.flows()
        mode = self.config.mode if self.config else "BOTH"
        target = self.config.target if self.config else None
        total = (None if any(flows[n] is None for n in ("GHF", "GLF")) else flows["GHF"] + flows["GLF"]) if mode == "BOTH" else flows[mode]
        self._snapshot = dict(active=self.active, state=self.state, reason=self.reason,
            mode=mode, target=target, total=total,
            error=None if total is None or target is None else target - total,
            ghf_actual=flows["GHF"], glf_actual=flows["GLF"],
            ghf_target=self.base if mode == "BOTH" else target if mode == "GHF" else 0.,
            glf_target=_clip(target - self.base, 10., 100.) if mode == "BOTH" and target is not None and self.base is not None else target if mode == "GLF" else 0.,
            fu_percent=self._commands["FU"], ghf_percent=self._commands["GHF"], glf_percent=self._commands["GLF"],
            glf_low_seconds=self._low_seconds, received_at=sample.received_at)
        return self.snapshot

    def update(self, sample, now):
        try:
            now = _finite(now, "Zeit")
        except ValueError as exc:
            self.fault("Ungültige Regelzeit: " + str(exc))
            return self.commands, self._make_snapshot(sample)
        if not self.active:
            return self.commands, self._make_snapshot(sample)
        cfg = self.config
        selected = ("GHF", "GLF") if cfg.mode == "BOTH" else (cfg.mode,)
        for name in selected:
            if not sample.valid(name, now, cfg.max_age_s):
                self._low_start = self._low_last = None
                self._low_seconds = 0.
                self.fault(f"{name}: Sensorstrom fehlt, ist ungültig oder veraltet.")
                return self.commands, self._make_snapshot(sample)
        flows = sample.flows()
        if "GLF" in selected:
            stamp = sample.received_at
            if flows["GLF"] < 5. - 1e-9:
                if self._low_last is None or stamp - self._low_last > cfg.max_age_s:
                    self._low_start = stamp
                if self._low_last is None or stamp > self._low_last:
                    self._low_last = stamp
                    self._low_seconds = max(0., stamp - self._low_start)
                if self._low_seconds >= cfg.glf_low_delay_s:
                    self.fault(f"GLF seit {cfg.glf_low_delay_s:g} Sekunden unter 5 m³/h. Magnetstellung und Luftweg prüfen.")
                    return self.commands, self._make_snapshot(sample)
            else:
                self._low_start = self._low_last = None
                self._low_seconds = 0.
        if sample.received_at == self._last_packet:
            return self.commands, self._make_snapshot(sample)
        self._last_packet = sample.received_at
        dt = now - self._last_time
        self._last_time = now
        if dt <= 0 or dt > cfg.max_gap_s:
            self.fault("Regeltakt unterbrochen oder ungültig · Simulationsbefehle auf 4 mA zurückgesetzt")
            return self.commands, self._make_snapshot(sample)

        try:
            if cfg.mode == "GLF":
                self._commands["GHF"] = 0.
            elif cfg.mode == "GHF":
                self._commands["GLF"] = 0.
            if cfg.mode == "BOTH":
                lower = max(50., cfg.target - 100.)
                upper = min(449., cfg.target - 10.)
                error = cfg.target - flows["GHF"] - flows["GLF"]
                l_position = self._commands["GLF"]
                g_position = self._commands["GHF"]
                # Zuerst fehlende Stellreserve behandeln. Eine kleine GLF-Menge
                # bedeutet bei bereits ganz geöffnetem GLF nicht, dass GHF
                # weiter geschlossen werden dürfte: Das würde den Gesamtfehler
                # vergrößern. Antiwindup allein kann diese Fehlverteilung nicht lösen.
                direction = 1 if self.base < lower else -1 if self.base > upper else 0
                immediate = direction != 0
                if not direction:
                    if l_position >= 98. and error > .2:
                        direction = 1
                    elif l_position <= 22. and error < -.2:
                        direction = -1
                    elif flows["GLF"] > cfg.preferred_high and l_position > 22. and g_position < 98.:
                        direction = 1
                    elif flows["GLF"] < cfg.preferred_low and l_position < 98. and g_position > 22.:
                        direction = -1
                # Innerhalb des zulässigen Bereichs kein Hinauslaufen zulassen.
                # Bei großem Sollsprung kann die bisherige Basis außerhalb der
                # NEUEN Grenzen liegen: dorthin rampen, nicht schlagartig springen.
                if (direction > 0 and self.base >= upper) or (direction < 0 and self.base <= lower):
                    direction = 0
                if direction != self._direction:
                    self._direction, self._direction_since = direction, now if direction else None
                if direction and (immediate or now - self._direction_since >= cfg.rebalance_delay_s):
                    self.base = _clip(self.base + direction * cfg.base_rate_m3h_s * dt, min(lower, self.base), max(upper, self.base))
                    self._fu_goal = self._simulation_feedforward(cfg)
            self._commands["FU"] += _clip(self._fu_goal - self._commands["FU"], -cfg.fu_rate_percent_s * dt, cfg.fu_rate_percent_s * dt)
            # Eingangsnormalisierung ausdrücklich getrennt von der Messkennlinie:
            # GHF-SP/450, GLF-SP/100; PV aus Sensorstrom auf 0…100 %.
            currents = sample.currents()
            g_sp = (self.base if cfg.mode == "BOTH" else cfg.target) / 450. * 100.
            l_sp = cfg.target - flows["GHF"] if cfg.mode == "BOTH" else cfg.target
            g_pv = (currents["GHF"] - .004) / .016 * 100. if currents["GHF"] is not None and math.isfinite(currents["GHF"]) else 0.
            l_pv = (currents["GLF"] - .004) / .016 * 100. if currents["GLF"] is not None and math.isfinite(currents["GLF"]) else 0.
            if self._need_reset:
                self._g_pid.reset(self._commands["GHF"], g_sp, g_pv)
                self._l_pid.reset(self._commands["GLF"], l_sp, l_pv)
                self._need_reset = False
            if cfg.mode in ("GHF", "BOTH"):
                self._commands["GHF"] = self._g_pid.update(g_sp, g_pv, dt)
            if cfg.mode in ("GLF", "BOTH"):
                self._commands["GLF"] = self._l_pid.update(l_sp, l_pv, dt)
            self.state = "running"
            self.reason = "GHF hält Grundlast · GLF korrigiert Gesamtfehler" if cfg.mode == "BOTH" else f"Nur {cfg.mode} wird geregelt"
            return self.commands, self._make_snapshot(sample)
        except (ArithmeticError, ValueError) as exc:
            self.fault("Reglerberechnung fehlgeschlagen: " + str(exc))
            return self.commands, self._make_snapshot(sample)



# ============================================================================
# DATENDIENST: Simulation oder ausschliesslich lesende NI-Messung.
# WICHTIG: In dieser Testversion existiert KEIN NI-Ausgangstask und KEIN
# NI-Schreibaufruf. Alle Stellwerte gehoeren ausschliesslich zum Simulationsmodell.
# ============================================================================
import copy
import importlib
import threading
from collections import deque

DEVICE = "cDAQ9183-251EC5D"
AI_CHANNELS = {"GHF": DEVICE + "Mod2/ai0", "GLF": DEVICE + "Mod2/ai1"}
AO_LABELS = {"FU": DEVICE + "Mod1/ao0", "GLF": DEVICE + "Mod1/ao1", "GHF": DEVICE + "Mod1/ao2"}
TEST_BUILD = "2026-11-17 / Testversion 2"


def _command_ma(name, percent):
    """Nur eine Anzeige berechnen; diese Funktion spricht keine Hardware an."""
    if percent is None:
        return None
    return 4.0 + 0.16 * percent if name == "FU" else 20.0 - 0.16 * percent


class AirflowService:
    """Ein Worker besitzt alle Quelldaten. Tk erhaelt nur kopierte Snapshots.

    Die Hardwareoption hat bewusst eine kleinere Faehigkeit als die Simulation:
    Sie liest ausschliesslich AI-Kanaele. Eine Schaltflaeche oder ein manipuliertes
    GUI-Objekt kann dadurch keine realen Analogausgaenge freischalten.
    """

    def __init__(self, mode="simulation", *, driver=None, autostart=True):
        if mode not in ("simulation", "hardware_readonly"):
            raise ValueError("Unbekannter Betriebsmodus.")
        self.mode = mode
        self._driver = driver
        self._lock = threading.RLock()
        self._shutdown = threading.Event()
        self._worker = None
        self._closed = False
        self._sample = FlowSample(0.0, None, None)
        self._last_numeric = {}
        self._commands = {"FU": 0.0, "GLF": 100.0, "GHF": 100.0}
        self._regulator = AirflowRegulator()
        self._plant = SimulatedPlant() if mode == "simulation" else None
        self._errors = deque(maxlen=30)
        self._episode_keys = set()
        self._error_number = 0
        self._source_error = None
        self._source_status = "Simulation wird gestartet." if self._plant else "NI-Messung wird verbunden. Keine realen Ausgaenge."
        if self._plant:
            initial = self._plant.sample()
            self._sample = FlowSample(time.monotonic(), initial.ghf_current_a, initial.glf_current_a)
        if autostart:
            self._start_worker()

    def _start_worker(self):
        self._worker = threading.Thread(
            target=self._run_simulation if self.mode == "simulation" else self._run_hardware,
            name="Luftsteuerung-Testdaten", daemon=True,
        )
        self._worker.start()

    def _error(self, key, title, message):
        """Eine Meldung pro Fehler-Episode statt eines Dialogs pro Messpaket."""
        if key in self._episode_keys:
            return
        self._episode_keys.add(key)
        self._error_number += 1
        self._errors.append({"id": self._error_number, "title": title, "message": str(message)})

    def _publish(self, sample):
        self._sample = sample
        self._source_error = None
        for name, current in sample.currents().items():
            key = "sensor:" + name
            if current is not None and math.isfinite(current):
                flow = sample.flows()[name]
                if flow is not None and math.isfinite(flow):
                    self._last_numeric[name] = (flow, current * 1000.0, sample.received_at)
                if not 0.0039 <= current <= 0.0201:
                    self._error(key, name + ": Sensorsignal", name + ": " + str(round(current * 1000, 6)) + " mA liegt ausserhalb der Teststand-Toleranz 3,9-20,1 mA.")
                else:
                    self._episode_keys.discard(key)
            else:
                self._error(key, name + ": Sensorsignal fehlt", name + ": Kein endlicher Sensorstrom verfuegbar. Der letzte numerische Wert wird als veraltet gekennzeichnet.")
        self._episode_keys.discard("source")

    def _run_simulation(self):
        previous = time.monotonic()
        while not self._shutdown.wait(0.05):
            now = time.monotonic()
            dt = now - previous
            previous = now
            try:
                with self._lock:
                    model_sample = self._plant.step(min(dt, 0.25), self._commands)
                    self._publish(FlowSample(now, model_sample.ghf_current_a, model_sample.glf_current_a))
                    if self._regulator.snapshot.get("active"):
                        commands, state = self._regulator.update(self._sample, now)
                        self._commands = dict(commands)
                        if state.get("state") == "FAULT" or (not state.get("active") and state.get("reason")):
                            self._error("regulation", "Regelung angehalten", state.get("reason", "Regelfehler. Simulierte Ausgaenge wurden auf 4 mA gesetzt."))
                    self._source_status = "Kuenstliches Anlagenmodell; keine reale FU-Kennlinie."
            except Exception as exc:
                with self._lock:
                    self._regulator.fault("Interner Simulationsfehler: " + str(exc))
                    self._commands = {"FU": 0.0, "GLF": 100.0, "GHF": 100.0}
                    self._error("internal", "Fehler im Teststand", str(exc))

    def _run_hardware(self):
        """Nur AI lesen. Kein AO-Task, kein write(), keine Start-/Endausgabe."""
        try:
            driver = self._driver or importlib.import_module("nidaqmx")
            constants = driver.constants
            with driver.Task() as task:
                for name in ("GHF", "GLF"):
                    task.ai_channels.add_ai_current_chan(
                        AI_CHANNELS[name], min_val=-0.020, max_val=0.020,
                        units=constants.CurrentUnits.AMPS,
                    )
                task.timing.cfg_samp_clk_timing(
                    rate=1000, sample_mode=constants.AcquisitionType.CONTINUOUS,
                    samps_per_chan=1000,
                )
                task.start()
                while not self._shutdown.is_set():
                    values = task.read(number_of_samples_per_channel=50, timeout=0.5)
                    if len(values) != 2:
                        raise RuntimeError("DAQ-Messpaket hat nicht genau zwei Eingangskanaele.")
                    averaged = []
                    for row in values:
                        seq = row if isinstance(row, (list, tuple)) else [row]
                        if not seq:
                            raise RuntimeError("Leeres DAQ-Messpaket.")
                        averaged.append(sum(float(v) for v in seq) / len(seq))
                    with self._lock:
                        self._publish(FlowSample(time.monotonic(), averaged[0], averaged[1]))
                        self._source_status = "NI-Sensoren verbunden. Hardware ausschliesslich lesend."
                    # Auch ein einfacher Ersatztreiber darf keine CPU-Endlosschleife erzeugen.
                    self._shutdown.wait(0.005)
        except Exception as exc:
            if not self._shutdown.is_set():
                with self._lock:
                    self._source_error = str(exc)
                    self._source_status = "NI-Messung nicht verfuegbar: " + str(exc)
                    self._error("source", "Hardware-Messung", str(exc) + "\n\nDieser Modus liest nur Sensoren. Es wurden keine Ausgangsbefehle gesendet. NI-Treiber, Kanalnamen und anderweitig belegte Tasks pruefen.")

    def retry_hardware(self):
        if self.mode != "hardware_readonly":
            return
        with self._lock:
            if self._closed:
                raise RuntimeError("Das Programm wird bereits geschlossen.")
            if self._worker and self._worker.is_alive():
                return
            self._episode_keys.discard("source")
            self._source_status = "Messung wird erneut verbunden."
            self._start_worker()

    def _require_simulation(self):
        if self._closed:
            raise RuntimeError("Der Teststand ist geschlossen.")
        if self.mode != "simulation":
            raise RuntimeError("Reale Ausgangsbefehle sind in dieser Testversion gesperrt. Hardwarebetrieb ist ausschliesslich lesend; vorhandene Ausgangswerte werden nicht veraendert.")

    def set_manual(self, commands):
        with self._lock:
            self._require_simulation()
            checked = {}
            for name, value in commands.items():
                if name not in ("FU", "GLF", "GHF"):
                    raise ValueError("Unbekannter Stellkanal.")
                if isinstance(value, bool):
                    raise ValueError("Sollwerte muessen Zahlen von 0 bis 100 sein.")
                value = float(value)
                if not math.isfinite(value) or not 0 <= value <= 100:
                    raise ValueError(name + ": Nur endliche Werte von 0 bis 100 %.")
                checked[name] = value
            if not checked:
                raise ValueError("Keine Sollwerte eingegeben.")
            self._regulator.stop("Manuelle Uebernahme; Automatik beendet.")
            self._commands.update(checked)
            self._episode_keys.discard("regulation")

    def start_regulation(self, mode, target):
        with self._lock:
            self._require_simulation()
            config = RegulatorConfig(mode=mode, target=float(target))
            if self._regulator.snapshot.get("active") and self._regulator.snapshot.get("mode") == mode:
                self._regulator.retarget(config.target)
            else:
                self._regulator.start(config, self._sample, time.monotonic(), self._commands)
            self._commands = dict(self._regulator.commands)
            self._episode_keys.discard("regulation")
            self._episode_keys.discard("internal")

    def stop_regulation(self):
        with self._lock:
            self._regulator.stop("Bewusster Regler-Stopp: letzte simulierte Stellwerte bleiben erhalten.")

    def set_fault(self, name, enabled):
        with self._lock:
            self._require_simulation()
            if name != "glf_closed":
                raise ValueError("Unbekannte Teststoerung.")
            self._plant.magnet_open = not bool(enabled)

    def snapshot(self):
        with self._lock:
            now = time.monotonic()
            flows = self._sample.flows()
            readings = {}
            for name, current in self._sample.currents().items():
                numeric = current is not None and math.isfinite(current)
                flow = flows[name] if numeric else None
                at = self._sample.received_at
                current_ma = current * 1000.0 if numeric else None
                problem = None
                if not numeric:
                    problem = "Kein endlicher Sensorstrom verfuegbar."
                    if name in self._last_numeric:
                        flow, old_ma, at = self._last_numeric[name]
                        problem += " Letzter Zahlenwert ist veraltet."
                elif not 0.0039 <= current <= 0.0201:
                    problem = "Sensorsignal ausserhalb 3,9-20,1 mA."
                if at <= 0 or now - at > 1.0:
                    problem = "Messwert fehlt oder ist aelter als 1000 ms." + (" " + problem if problem else "")
                if self._source_error:
                    problem = "Messquelle ausgefallen: " + self._source_error + (" " + problem if problem else "")
                readings[name] = {"flow": flow, "current_ma": current_ma, "at": at, "error": problem}
            state = dict(self._regulator.snapshot)
            selected = ("GHF", "GLF") if state.get("mode", "BOTH") == "BOTH" else (state["mode"],)
            total = None if any(flows[name] is None for name in selected) else sum(flows[name] for name in selected)
            target = state.get("target")
            state.update(
                total=total, ghf_actual=flows["GHF"], glf_actual=flows["GLF"],
                error=None if target is None or total is None else target - total,
                received_at=self._sample.received_at,
                fu_percent=self._commands["FU"], ghf_percent=self._commands["GHF"], glf_percent=self._commands["GLF"],
            )
            if self.mode == "hardware_readonly":
                state.update(active=False, state="READ_ONLY", reason="Keine reale automatische Regelung in dieser Testversion.")
            outputs = {
                name: {"percent": self._commands[name] if self.mode == "simulation" else None,
                       "current_ma": _command_ma(name, self._commands[name]) if self.mode == "simulation" else None}
                for name in ("FU", "GLF", "GHF")
            }
            return copy.deepcopy({
                "mode": self.mode, "readings": readings, "outputs": outputs,
                "regulation": state, "errors": list(self._errors),
                "source_status": self._source_status,
                "capabilities": {"fault_injection": self.mode == "simulation"},
                "settings": {
                    "build": TEST_BUILD, "device": DEVICE,
                    "channels": {"AO": AO_LABELS, "AI": AI_CHANNELS},
                    "pid": {
                        "GHF": {"kc": 0.900, "ti_min": 0.108, "td_min": 0.007},
                        "GLF": {"kc": 0.320, "ti_min": 0.075, "td_min": 0.001},
                    },
                    "total_range": "92-549 m3/h (gewuenschter Bereich, kein Anlagen-Nachweis)",
                    "tolerance": "+/-20 m3/h vorlaeufig",
                    "curve": "NUR SIMULATION: kuenstliche FU-Kennlinie. Reale Gesamtkennlinie offen.",
                    "legacy": "PID mit uebernommenen Parametern; keine nachgewiesene 1:1-LabVIEW-Umsetzung.",
                    "source": "Eigenstaendiger Teststand. Neuester Nutzerupload derzeit nicht abrufbar.",
                },
            })

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self.mode == "simulation":
                self._regulator.stop("Teststand wird geschlossen.")
                self._commands = {"FU": 0.0, "GLF": 100.0, "GHF": 100.0}
            self._shutdown.set()
        if self._worker and self._worker is not threading.current_thread():
            self._worker.join(timeout=1.0)



# Oberfläche der Testversion. Tk wird erst beim Aufbau importiert, damit
# --help und reine Logiktests keine grafische Umgebung benötigen.
class _AirflowTrend:
    """Kleines Canvas-Diagramm; liest niemals selbst Sensoren aus."""
    def __init__(self, parent, tk, title, fonts, color):
        self.color = color
        self.fonts = fonts
        self.frame = tk.Frame(parent, bg="#FFFFFF", highlightbackground="#CAD9E5", highlightthickness=1)
        self.frame.grid_columnconfigure(0, weight=1)
        self.frame.grid_rowconfigure(1, weight=1)
        self.heading = tk.Label(self.frame, text=title, bg="#FFFFFF", fg="#142D3B",
                                font=fonts["heading"], anchor="w")
        self.heading.grid(row=0, column=0, sticky="ew", padx=9, pady=(5, 0))
        self.canvas = tk.Canvas(self.frame, bg="#F6F9FD", highlightthickness=0, width=100, height=60)
        self.canvas.grid(row=1, column=0, sticky="nsew", padx=5, pady=(0, 5))

    def draw(self, samples, now, show_target=False, live=True, compact=False):
        import math
        c = self.canvas
        if not c.winfo_ismapped():
            return
        w, h = c.winfo_width(), c.winfo_height()
        if w < 70 or h < 25:
            return
        c.delete("all")
        data = [p for p in samples if now - 60 <= p[0] <= now]
        numbers = [v for p in data for v in ((p[1], p[2]) if show_target else (p[1],))
                   if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)]
        font = self.fonts["axis"]
        if not numbers:
            c.create_text(w/2, h/2, text="Noch keine Messdaten", fill="#536979", font=font)
            return
        low, high = min(numbers), max(numbers)
        margin = max(1.0, (high-low)*.2)
        low -= margin
        high += margin
        line_height = font.metrics("linespace")
        left = max(font.measure(f"{low:.1f}"), font.measure(f"{high:.1f}"))+9
        right, top, bottom = w-9, line_height+4, h-line_height*2-8
        if bottom-top < 25:
            top, bottom = 4, h-5
            axes = False
        else:
            axes = True
        if right <= left or bottom <= top:
            return
        def point(t, value):
            return left+(t-(now-60))/60*(right-left), bottom-(value-low)/(high-low)*(bottom-top)
        if axes:
            for i in range(3):
                value = low+(high-low)*i/2
                y = point(now, value)[1]
                c.create_line(left,y,right,y,fill="#DDE6EF")
                c.create_text(left-5,y,text=f"{value:.1f}".replace(".",","),anchor="e",font=font,fill="#536979")
            for seconds in (-60,-45,-30,-15,0):
                x = left+(seconds+60)/60*(right-left)
                c.create_line(x,top,x,bottom,fill="#E6EDF4")
                c.create_text(x,bottom+3,text=str(seconds),anchor="n",font=font,fill="#536979")
            c.create_text(left,0,text="m³/h",anchor="nw",font=font,fill="#536979")
            c.create_text((left+right)/2,h-1,text="Zeit [s]",anchor="s",font=font,fill="#536979")
            if show_target and right-left > 220:
                c.create_line(right-115,line_height/2,right-94,line_height/2,fill="#263F85",dash=(5,3))
                c.create_text(right-90,line_height/2,text="Soll",anchor="w",font=font,fill="#536979")
                c.create_line(right-48,line_height/2,right-27,line_height/2,fill=self.color,width=2)
                c.create_text(right-23,line_height/2,text="Ist",anchor="w",font=font,fill="#536979")
        # Punktformat: Originalzeit, Messwert, Sollwert, Fehlerkennzeichnung.
        # Lücken und alte Daten nicht künstlich mit aktuellen Daten verbinden.
        for index in ((2,1) if show_target else (1,)):
            previous = None
            for p in data:
                value = p[index]
                finite = isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)
                if finite and previous is not None and p[0]-previous[0] <= 1.5:
                    a = point(previous[0], previous[index])
                    b = point(p[0],value)
                    if index == 2:
                        middle = point(p[0],previous[index])
                        c.create_line(*a,*middle,*b,fill="#263F85",dash=(5,3),width=1.5)
                    else:
                        color = "#B42332" if p[3] or previous[3] else self.color
                        c.create_line(*a,*b,fill=color,width=2)
                previous = p if finite else None
        if live and data and data[-1][1] is not None and now-data[-1][0] <= 1.5:
            p = data[-1]
            value = p[1]
            if not isinstance(value,(int,float)) or not math.isfinite(value):
                return
            x,y = point(p[0],value)
            color = "#B42332" if p[3] else self.color
            c.create_oval(x-3,y-3,x+3,y+3,fill=color,outline="#FFFFFF")
            label = f"{value:.2f} m³/h".replace(".",",")
            badge = self.fonts["badge"]
            bw,bh = badge.measure(label)+12,badge.metrics("linespace")+6
            if bw < right-left and bh < bottom-top:
                bx = max(left,min(right-bw,x-bw+4))
                by = max(2,min(bottom-bh,y-bh-8))
                c.create_rectangle(bx,by,bx+bw,by+bh,fill="#E0F4EE" if not p[3] else "#FBE5E5",outline="#B9D7D0")
                c.create_text(bx+bw/2,by+bh/2,text=label,font=badge,fill="#142D3B")


class AirflowApp:
    """Drei Seiten; der Service besitzt Messung, Simulation und Regelung."""
    def __init__(self, root, service):
        import tkinter as tk
        from tkinter import ttk, messagebox, font
        import collections
        self.tk,self.ttk,self.messagebox = tk,ttk,messagebox
        self.root,self.service = root,service
        self._after = None
        self._closing = self._dialog_active = False
        self._snapshot = {}
        self._last_values = {}
        self._display = {}
        self._seen_errors = set()
        self._seen_order = collections.deque()
        self._events = collections.deque(maxlen=200)
        self._last_stamp = {name:None for name in ("GHF","GLF","TOTAL")}
        self._last_reg_mode = None
        self._history = {name:collections.deque(maxlen=1800) for name in ("GHF","GLF","TOTAL")}
        self._reg_history = {name:collections.deque(maxlen=1800) for name in ("GHF","GLF","TOTAL")}
        self._compact = None
        self._selected_page = "Steuerung"
        root.title("Nassheuer | Luftsteuerung | Testversion 2")
        root.geometry("1366x768")
        root.minsize(1024,700)
        root.configure(bg="#F2F6FA")
        root.grid_columnconfigure(0,weight=1)
        root.grid_rowconfigure(2,weight=1)
        available={name.casefold():name for name in font.families(root)}
        family=next((available[name.casefold()] for name in
                     ("Segoe UI","DejaVu Sans","Arial","Helvetica")
                     if name.casefold() in available),
                    font.nametofont("TkDefaultFont",root=root).actual("family"))
        self.fonts = {
            "brand":font.Font(family=family,size=16,weight="bold"),
            "title":font.Font(family=family,size=16,weight="bold"),
            "heading":font.Font(family=family,size=12,weight="bold"),
            "body":font.Font(family=family,size=10),
            "bold":font.Font(family=family,size=10,weight="bold"),
            "small":font.Font(family=family,size=9),
            "value":font.Font(family=family,size=27,weight="bold"),
            "entry":font.Font(family=family,size=13,weight="bold"),
            "axis":font.Font(family=family,size=-11),
            "badge":font.Font(family=family,size=-12,weight="bold"),
            "process":font.Font(family=family,size=-15),
            "process_bold":font.Font(family=family,size=-19,weight="bold"),
            "process_small":font.Font(family=family,size=-11),
        }
        style=ttk.Style(root)
        if "clam" in style.theme_names():style.theme_use("clam")
        style.configure("TButton",font=self.fonts["bold"],padding=(9,6),background="#EDF3F9",foreground="#142D3B")
        style.map("TButton",background=[("active","#DDE9F3")])
        style.configure("Primary.TButton",background="#008C83",foreground="#FFFFFF")
        style.map("Primary.TButton",background=[("disabled","#DCE5EB"),("active","#00766E")],foreground=[("disabled","#687B89")])
        style.configure("TSpinbox",font=self.fonts["entry"],padding=3)
        style.configure("TRadiobutton",background="#FFFFFF",foreground="#142D3B",font=self.fonts["body"])
        self._build_header()
        self.pages={name:tk.Frame(root,bg="#F2F6FA") for name in ("Steuerung","Regelung","Einstellungen")}
        self._build_control(self.pages["Steuerung"])
        self._build_regulation(self.pages["Regelung"])
        self._build_settings(self.pages["Einstellungen"])
        footer=tk.Frame(root,bg="#F2F6FA")
        footer.grid(row=3,column=0,sticky="ew",padx=12,pady=(3,8))
        footer.grid_columnconfigure(0,weight=1)
        self._label(footer,"Testversion · keine realen Ausgangsbefehle",font="small").grid(row=0,column=0,sticky="w")
        ttk.Button(footer,text="Programm schließen",command=self._on_close).grid(row=0,column=1)
        root.protocol("WM_DELETE_WINDOW",self._on_close)
        root.bind("<Configure>",self._resize,add="+")
        self._show_page("Steuerung")
        self._poll()

    def _label(self,parent,text="",font="body",**kw):
        return self.tk.Label(parent,text=text,font=self.fonts[font],bg=kw.pop("bg",parent.cget("bg")),fg=kw.pop("fg","#142D3B"),**kw)

    def _panel(self,parent):
        return self.tk.Frame(parent,bg="#FFFFFF",highlightbackground="#CAD9E5",highlightthickness=1)

    def _build_header(self):
        header=self.tk.Frame(self.root,bg="#FFFFFF")
        header.grid(row=0,column=0,sticky="ew")
        header.grid_columnconfigure(0,weight=1)
        self._label(header,"NASSHEUER / LUFTSTEUERUNG",font="brand").grid(row=0,column=0,sticky="w",padx=12,pady=10)
        self._nav={}
        for col,name in enumerate(("Steuerung","Regelung","Einstellungen"),start=1):
            b=self.ttk.Button(header,text=name,command=lambda n=name:self._show_page(n))
            b.grid(row=0,column=col,padx=3,pady=6);self._nav[name]=b
        self._banner=self._label(self.root,"SIMULATION · Testversion · keine realen Ausgangsbefehle",font="bold",bg="#DCEFF4",fg="#126B80",anchor="w",padx=12,pady=6)
        self._banner.grid(row=1,column=0,sticky="ew",pady=(0,4))

    def _show_page(self,name):
        self._selected_page=name
        for key,page in self.pages.items():
            if key==name:page.grid(row=2,column=0,sticky="nsew",padx=10,pady=4)
            else:page.grid_remove()
            self._nav[key].configure(style="Primary.TButton" if key==name else "TButton")

    def _build_control(self,page):
        page.grid_columnconfigure(0,weight=1)
        page.grid_rowconfigure(0,weight=10,uniform="control_sections")
        page.grid_rowconfigure(1,weight=10,uniform="control_sections")
        top=self.tk.Frame(page,bg="#F2F6FA")
        top.grid(row=0,column=0,sticky="nsew",pady=(0,7))
        top.grid_rowconfigure(0,weight=1)
        top.grid_columnconfigure(0,weight=27,uniform="control_columns")
        top.grid_columnconfigure(1,weight=73,uniform="control_columns")
        cards=self.tk.Frame(top,bg="#F2F6FA")
        cards.grid(row=0,column=0,sticky="nsew",padx=(0,7))
        cards.grid_columnconfigure(0,weight=1)
        cards.grid_rowconfigure((0,1),weight=1,uniform="readings")
        self._reading_widgets={}
        for row,name in enumerate(("GHF","GLF")):
            p=self._panel(cards);p.configure(bg="#EDF4FA")
            p.grid(row=row,column=0,sticky="nsew",pady=(0,3) if row==0 else (3,0))
            self._label(p,f"{name} – Volumenstrom",font="heading").pack(anchor="w",padx=10,pady=(5,1))
            value=self._label(p,"— m³/h",font="value");value.pack(anchor="w",padx=10)
            current=self._label(p,"Sensorstrom: — mA");current.pack(anchor="w",padx=10,pady=(2,0))
            age=self._label(p,"Messwertalter: —",font="small",fg="#126B80");age.pack(anchor="w",padx=10,pady=(1,0))
            state=self._label(p,"Warte auf Messdaten",font="small");state.pack(anchor="w",padx=10,pady=(0,3))
            self._reading_widgets[name]=(value,current,age,state)
        panel=self._panel(top);panel.grid(row=0,column=1,sticky="nsew")
        panel.grid_columnconfigure(0,weight=1)
        self._label(panel,"Manuelle Stellbefehle",font="title").grid(row=0,column=0,sticky="w",padx=10,pady=(5,4))
        table=self.tk.Frame(panel,bg="#CAD9E5");table.grid(row=1,column=0,sticky="ew",padx=9)
        table.grid_columnconfigure(0,weight=1)
        self._manual_vars={};self._manual_entries={};self._last_commands={}
        for row in range(4):
            band=self.tk.Frame(table,bg="#DDE7F0" if row==0 else "#F5F8FC" if row%2 else "#EDF3F9")
            band.grid(row=row,column=0,sticky="ew",padx=1,pady=1)
            for col,weight in enumerate((20,40,40)):band.grid_columnconfigure(col,weight=weight,uniform="manual_columns")
            if row==0:
                for col,title in enumerate(("Kanal","Sollwert [%]","Letzter Befehl")):
                    self._label(band,title,font="bold").grid(row=0,column=col,sticky="w",padx=10,pady=3)
                continue
            name=("FU","GLF","GHF")[row-1]
            self._label(band,name,font="heading").grid(row=0,column=0,sticky="w",padx=10,pady=6)
            field=self.tk.Frame(band,bg=band.cget("bg"));field.grid(row=0,column=1,sticky="w",padx=10)
            var=self.tk.StringVar(master=self.root,value="")
            entry=self.ttk.Spinbox(field,from_=0,to=100,increment=1,textvariable=var,width=8,font=self.fonts["entry"])
            entry.grid(row=0,column=0);self._label(field,"%",font="bold").grid(row=0,column=1,padx=6)
            self._manual_vars[name]=var;self._manual_entries[name]=entry
            last=self._label(band,"— mA");last.grid(row=0,column=2,sticky="w",padx=10);self._last_commands[name]=last
        self._label(panel,"Befehlswerte · keine Ausgangsstrommessung",font="small").grid(row=2,column=0,sticky="w",padx=11,pady=(4,0))
        actions=self.tk.Frame(panel,bg="#FFFFFF");actions.grid(row=3,column=0,sticky="ew",padx=9,pady=5)
        actions.grid_columnconfigure((0,1),weight=1,uniform="manual_actions")
        self._manual_apply=self.ttk.Button(actions,text="▶  Sollwerte übernehmen",style="Primary.TButton",command=self._manual_submit)
        self._manual_apply.grid(row=0,column=0,sticky="ew",padx=(0,4))
        self._manual_clear=self.ttk.Button(actions,text="Eingaben leeren",command=self._manual_clear_inputs)
        self._manual_clear.grid(row=0,column=1,sticky="ew",padx=(4,0))
        self._manual_message=self._label(panel,"Leere Felder bleiben unverändert.",font="small",anchor="w")
        self._manual_message.grid(row=4,column=0,sticky="ew",padx=11,pady=(0,4))
        panel.grid_rowconfigure(5,weight=1)
        plots=self.tk.Frame(page,bg="#F2F6FA");plots.grid(row=1,column=0,sticky="nsew")
        plots.grid_columnconfigure((0,1),weight=1,uniform="manual_plots");plots.grid_rowconfigure(0,weight=1)
        self._control_charts={}
        for col,name in enumerate(("GHF","GLF")):
            chart=_AirflowTrend(plots,self.tk,f"{name} · Durchflussverlauf",self.fonts,"#263F85" if name=="GHF" else "#176FD1")
            chart.frame.grid(row=0,column=col,sticky="nsew",padx=(0,4) if col==0 else (4,0));self._control_charts[name]=chart

    def _build_regulation(self,page):
        page.grid_columnconfigure(0,weight=1)
        page.grid_rowconfigure(2,weight=10,uniform="reg_sections")
        page.grid_rowconfigure(3,weight=12,uniform="reg_sections")
        title=self.tk.Frame(page,bg="#F2F6FA");title.grid(row=0,column=0,sticky="ew",pady=(0,4));title.grid_columnconfigure(0,weight=1)
        self._label(title,"Regelung · Luftwege im Blick",font="title").grid(row=0,column=0,sticky="w",padx=3)
        self._reg_state=self._label(title,"Bereit",font="bold",bg="#E1EDF4",padx=12,pady=4)
        self._reg_state.grid(row=0,column=1,padx=5)
        self.ttk.Button(title,text="Parameter",command=lambda:self._show_page("Einstellungen")).grid(row=0,column=2)
        control=self._panel(page);control.grid(row=1,column=0,sticky="ew",pady=(0,6))
        control.grid_columnconfigure(1,weight=1);control.grid_columnconfigure((5,6),weight=1)
        self._label(control,"Betriebsart",font="bold").grid(row=0,column=0,padx=(8,3),pady=7)
        mode_box=self.tk.Frame(control,bg="#FFFFFF");mode_box.grid(row=0,column=1,sticky="w",padx=3)
        self._mode=self.tk.StringVar(master=self.root,value="BOTH")
        self._mode_buttons=[]
        for col,(key,label) in enumerate((("GLF","Nur GLF"),("GHF","Nur GHF"),("BOTH","Beide"))):
            b=self.ttk.Radiobutton(mode_box,text=label,value=key,variable=self._mode);b.grid(row=0,column=col,padx=3);self._mode_buttons.append(b)
        self._label(control,"Gesamt-Sollwert",font="bold").grid(row=0,column=2,padx=(7,3))
        self._target=self.tk.StringVar(master=self.root,value="")
        self._target_entry=self.ttk.Spinbox(control,from_=0,to=549,increment=1,textvariable=self._target,width=8,font=self.fonts["entry"])
        self._target_entry.grid(row=0,column=3,padx=3)
        self._label(control,"m³/h").grid(row=0,column=4,padx=(2,7))
        self._reg_apply=self.ttk.Button(control,text="Regelung starten",style="Primary.TButton",command=self._reg_submit)
        self._reg_apply.grid(row=0,column=5,sticky="ew",padx=(3,4))
        self._reg_stop=self.ttk.Button(control,text="Regelung anhalten",command=self._reg_stop_submit)
        self._reg_stop.grid(row=0,column=6,sticky="ew",padx=(4,8))
        process=self._panel(page);process.grid(row=2,column=0,sticky="nsew",pady=(0,6));process.grid_columnconfigure(0,weight=1);process.grid_rowconfigure(0,weight=1)
        self._process=self.tk.Canvas(process,bg="#F6F9FD",highlightthickness=0,width=100,height=110)
        self._process.grid(row=0,column=0,sticky="nsew",padx=4,pady=4)
        plots=self.tk.Frame(page,bg="#F2F6FA");plots.grid(row=3,column=0,sticky="nsew")
        plots.grid_columnconfigure(0,weight=57,uniform="reg_plots");plots.grid_columnconfigure(1,weight=43,uniform="reg_plots");plots.grid_rowconfigure(0,weight=1)
        total=_AirflowTrend(plots,self.tk,"Gesamt · Soll / Ist",self.fonts,"#008C83")
        total.frame.grid(row=0,column=0,sticky="nsew",padx=(0,4))
        right=self.tk.Frame(plots,bg="#F2F6FA");right.grid(row=0,column=1,sticky="nsew",padx=(4,0));right.grid_columnconfigure(0,weight=1);right.grid_rowconfigure((0,1),weight=1,uniform="reg_branches")
        self._reg_charts={"TOTAL":total}
        for row,name in enumerate(("GHF","GLF")):
            chart=_AirflowTrend(right,self.tk,f"{name} · Durchflussverlauf",self.fonts,"#263F85" if name=="GHF" else "#176FD1")
            chart.frame.grid(row=row,column=0,sticky="nsew",pady=(0,3) if row==0 else (3,0));self._reg_charts[name]=chart
        self._reg_reason=self._label(page,"Simulation bereit.",font="small",anchor="w")
        self._reg_reason.grid(row=4,column=0,sticky="ew",padx=3,pady=(2,0))

    def _build_settings(self,page):
        page.grid_columnconfigure(0,weight=1);page.grid_rowconfigure(4,weight=1)
        self._label(page,"Einstellungen und Teststatus",font="title").grid(row=0,column=0,sticky="w",padx=3,pady=(0,5))
        self._settings_text=self._label(page,"Parameter werden vom Service übernommen.",anchor="w",justify="left")
        self._settings_text.grid(row=1,column=0,sticky="ew",padx=5,pady=5)
        self._channels=self._label(page,"",anchor="w",justify="left",font="small")
        self._channels.grid(row=2,column=0,sticky="ew",padx=5,pady=5)
        self._fault_var=self.tk.BooleanVar(master=self.root,value=False)
        self._fault_check=self.ttk.Checkbutton(page,text="GLF-Luftweg geschlossen simulieren",variable=self._fault_var,command=self._set_fault)
        self._fault_check.grid(row=3,column=0,sticky="w",padx=5,pady=5)
        self._retry_hardware=self.ttk.Button(page,text="Messung neu verbinden",command=self._retry_hardware_start)
        self._retry_hardware.grid(row=3,column=0,sticky="e",padx=5,pady=5)
        log_panel=self._panel(page);log_panel.grid(row=4,column=0,sticky="nsew",pady=(5,0));log_panel.grid_columnconfigure(0,weight=1);log_panel.grid_rowconfigure(1,weight=1)
        self._label(log_panel,"Ereignisprotokoll",font="heading").grid(row=0,column=0,sticky="w",padx=8,pady=5)
        self._log_text=self.tk.Text(log_panel,wrap="word",font=self.fonts["small"],height=10,bg="#F6F9FD",fg="#142D3B",relief="flat",state="disabled")
        self._log_text.grid(row=1,column=0,sticky="nsew",padx=8,pady=(0,8))

    @staticmethod
    def _number(value,digits=2):
        import math
        return f"{value:.{digits}f}".replace(".",",") if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) else "—"

    @staticmethod
    def _regulation_indicator(reg,total,trustworthy,hardware=False):
        """Nur Darstellung: Regelzustand und gemessene Abweichung getrennt bewerten."""
        import math
        if hardware:
            return "Nur Lesen · Automatik aus","#536979","#E1EDF4"
        state=str(reg.get("state","idle"))
        if state=="error":
            return "Regelungsfehler","#B42332","#FBE5E5"
        if reg.get("active") and state=="running":
            target=reg.get("target")
            finite=lambda v:isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)
            if not trustworthy or not finite(total) or not finite(target):
                return "Rückmeldung prüfen","#986000","#FFF0D5"
            if abs(total-target)>20.0:
                return "Außerhalb ±20 m³/h","#986000","#FFF0D5"
            return "Innerhalb ±20 m³/h","#167450","#DEF3E5"
        label={"idle":"Bereit","waiting":"Warte auf Messbereich","paused":"Regelung angehalten"}.get(state,state)
        return label,"#986000" if state=="waiting" else "#536979","#E1EDF4"

    def _reading(self,name,data,now):
        import math
        raw=dict(data.get("readings",{}).get(name,{}) or {})
        value,current,stamp=raw.get("flow"),raw.get("current_ma"),raw.get("at",0.) or 0.
        finite=lambda v:isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)
        if not finite(value) and finite(current):value=(current-4.)/(.0356 if name=="GHF" else .16)
        cached=False
        if finite(value) and finite(current) and stamp>0:
            self._last_values[name]=dict(flow=value,current_ma=current,at=stamp,error=raw.get("error"))
        elif finite(value) and stamp>0:
            cached=True
            current=self._last_values.get(name,{}).get("current_ma")
        elif name in self._last_values:
            old=self._last_values[name];value=old["flow"];stamp=old["at"];cached=True
            if not finite(current):current=old.get("current_ma")
        age=max(0,round((now-stamp)*1000)) if stamp else None
        cached=cached or age is None or age>1500
        error=raw.get("error")
        below=finite(value) and value<(50. if name=="GHF" else 10.)
        above=finite(value) and value>(449. if name=="GHF" else 100.)
        color="#B42332" if error else "#986000" if cached or below or above else "#142D3B"
        status=str(error) if error else "Letzter Wert · veraltet" if cached else "Unter Messbereich" if below else "Über Messbereich" if above else "Aktueller Messwert"
        return dict(flow=value if finite(value) else None,current_ma=current,at=stamp,age=age,cached=cached,error=error,color=color,status=status)

    def _render(self,snapshot,now):
        self._snapshot=snapshot
        hardware=snapshot.get("mode")=="hardware_readonly"
        build=str((snapshot.get("settings",{}) or {}).get("build","Testversion 2"))
        if build!=getattr(self,"_shown_build",None):
            self.root.title("Nassheuer | Luftsteuerung | "+build)
            self._shown_build=build
        self._banner.configure(text="HARDWARE · NUR LESEN · keine realen Ausgangsbefehle · vorhandene Ausgangswerte bleiben unverändert" if hardware else "SIMULATION · "+build+" · keine realen Ausgangsbefehle",bg="#FFF0D5" if hardware else "#DCEFF4",fg="#805100" if hardware else "#126B80")
        self._display={name:self._reading(name,snapshot,now) for name in ("GHF","GLF")}
        reg=snapshot.get("regulation",{}) or {}
        mode=reg.get("mode","BOTH")
        if mode not in ("GLF","GHF","BOTH"):mode="BOTH"
        if mode!=self._last_reg_mode:
            for history in self._reg_history.values():history.clear()
            self._last_stamp["TOTAL"]=None;self._last_reg_mode=mode
        for name,r in self._display.items():
            value,current,age,state=self._reading_widgets[name]
            value.configure(text=self._number(r["flow"])+" m³/h",fg=r["color"])
            current.configure(text="Sensorstrom: "+self._number(r["current_ma"],6 if r["error"] else 3)+" mA",fg=r["color"])
            age.configure(text="Messwertalter: "+("—" if r["age"] is None else str(r["age"])+" ms")+(" · ALT" if r["cached"] else ""),fg=r["color"])
            state.configure(text=r["status"][:45],fg=r["color"])
            out=snapshot.get("outputs",{}).get(name,{}) or {}
            self._last_commands[name].configure(text=self._number(out.get("current_ma"),3)+" mA")
            if r["at"] and not r["cached"] and r["at"]!=self._last_stamp[name]:
                self._last_stamp[name]=r["at"]
                point=(r["at"],r["flow"],reg.get(name.lower()+"_target"),bool(r["error"]))
                self._history[name].append(point);self._reg_history[name].append(point)
        fu=snapshot.get("outputs",{}).get("FU",{}) or {}
        self._last_commands["FU"].configure(text=self._number(fu.get("current_ma"),3)+" mA")
        active=("GHF","GLF") if mode=="BOTH" else (mode,)
        values=[self._display[n]["flow"] for n in active]
        total=sum(values) if all(v is not None for v in values) else None
        stamp=max((self._display[n]["at"] for n in active),default=0.)
        stale=any(self._display[n]["cached"] for n in active)
        faulty=any(self._display[n]["error"] for n in active)
        if stamp and stamp!=self._last_stamp["TOTAL"] and not stale:
            self._last_stamp["TOTAL"]=stamp
            self._history["TOTAL"].append((stamp,total,reg.get("target"),faulty))
            self._reg_history["TOTAL"].append((stamp,total,reg.get("target"),faulty))
        for history in list(self._history.values())+list(self._reg_history.values()):
            while history and history[0][0]<now-60:history.popleft()
        running=bool(reg.get("active"))
        trustworthy=not stale and not faulty and all(
            self._display[n]["flow"] is not None and
            (50.0 if n=="GHF" else 10.0)<=self._display[n]["flow"]<=(449.0 if n=="GHF" else 100.0)
            for n in active)
        self._total_trustworthy=trustworthy
        label,fg,bg=self._regulation_indicator(reg,total,trustworthy,hardware)
        self._reg_state.configure(text=label,fg=fg,bg=bg)
        reason=str(reg.get("reason","") or "Gesamtsollwert eingeben.")
        if reg.get("glf_low_seconds",0)>0:reason+=" · GLF unter 5: "+self._number(reg["glf_low_seconds"],1)+"/10 s"
        if hardware:reason="Hardwaremodus liest nur Sensoren. Vorhandene reale Ausgangswerte werden beim Starten und Schließen nicht geändert."
        self._reg_reason.configure(text=reason[:135])
        self._reg_apply.configure(text=("Übernehmen" if running else "Starten") if self._compact else ("Sollwert übernehmen" if running else "Regelung starten"))
        self._reg_stop.configure(text="Anhalten" if self._compact else "Regelung anhalten")
        for button in (self._manual_apply,self._manual_clear,self._reg_apply):button.state(["disabled"] if hardware else ["!disabled"])
        self._reg_stop.state(["!disabled"] if running and not hardware else ["disabled"])
        for widget in list(self._manual_entries.values())+[self._target_entry]+self._mode_buttons:widget.state(["disabled"] if hardware else ["!disabled"])
        capability=bool(snapshot.get("capabilities",{}).get("fault_injection")) and hasattr(self.service,"set_fault") and not hardware
        self._fault_check.state(["!disabled"] if capability else ["disabled"])
        self._retry_hardware.state(["!disabled"] if hardware else ["disabled"])
        self._render_settings(snapshot)
        if self._selected_page=="Steuerung":
            for name,chart in self._control_charts.items():chart.draw(self._history[name],now,live=not self._display[name]["cached"],compact=self._compact)
        elif self._selected_page=="Regelung":
            self._draw_process(reg,total,stale,now)
            for name,chart in self._reg_charts.items():
                live=not stale if name=="TOTAL" else not self._display[name]["cached"]
                chart.draw(self._reg_history[name],now,show_target=True,live=live,compact=self._compact)

    def _draw_process(self,reg,total,stale,now):
        import math
        c=self._process;w,h=c.winfo_width(),c.winfo_height()
        if w<100 or h<45:return
        c.delete("all");scale=max(.65,min(w/1300,h/210,1.35))
        for key,size in (("process",15),("process_bold",20),("process_small",11)):
            self.fonts[key].configure(size=-max(9,round(size*scale)))
        def text(x,y,value,font="process",anchor="center",color="#142D3B"):
            c.create_text(x*w,y*h,text=value,font=self.fonts[font],anchor=anchor,fill=color)
        def box(x0,y0,x1,y1,active=True):
            l,t,r,b=x0*w,y0*h,x1*w,y1*h;d=min(8*scale,(b-t)/5)
            p=(l+d,t,r-d,t,r,t,r,t+d,r,b-d,r,b,r-d,b,l+d,b,l,b,l,b-d,l,t+d,l,t)
            c.create_polygon(*p,smooth=True,splinesteps=20,fill="#EDF4FA" if active else "#EFF1F3",outline="#BDCCDC")
        mode=reg.get("mode","BOTH")
        for name,y in (("GHF",.23),("GLF",.70)):
            active=mode in (name,"BOTH");color="#008C83" if active else "#AAB8C4"
            c.create_line(.225*w,.48*h,.265*w,.48*h,.265*w,y*h,.29*w,y*h,fill=color,width=2,arrow="last")
            c.create_line(.74*w,y*h,.78*w,y*h,.78*w,.48*h,.82*w,.48*h,fill=color,width=2,arrow="last")
        box(.015,.31,.225,.65)
        cx,cy=.044*w,.48*h;r=min(.024*w,.12*h)
        c.create_oval(cx-r,cy-r,cx+r,cy+r,fill="#008C83",outline="")
        for angle in (0,120,240):
            a=math.radians(angle);p=[]
            for dr,da in ((.12,-25),(.76,-20),(.85,17),(.23,52)):
                p.extend((cx+r*dr*math.cos(a+math.radians(da)),cy+r*dr*math.sin(a+math.radians(da))))
            c.create_polygon(*p,smooth=True,fill="#FFFFFF",outline="")
        text(.074,.43,"FU · Lüfter" if self._compact else "FU · Gemeinsamer Lüfter","process_small","w")
        fu=self._snapshot.get("outputs",{}).get("FU",{}) or {}
        text(.074,.56,"Befehl: "+self._number(fu.get("percent"))+" %","process_small","w",color="#536979")
        for name,y0,y1 in (("GHF",.07,.39),("GLF",.54,.86)):
            active=mode in (name,"BOTH");r=self._display[name];mid=(y0+y1)/2
            box(.29,y0,.74,y1,active)
            text(.305,mid-.045,name,"process_bold","w")
            text(.305,mid+.09,("Grundlast" if name=="GHF" else "Feinregelung") if active else "nicht verwendet","process_small","w",color="#536979")
            for x,label in ((.415,"Teilsoll"),(.525,"Istwert"),(.627,"Sensorstrom"),(.708,"Alter")):
                text(x,y0+.06,label,"process_small")
            text(.415,mid+.04,self._number(reg.get(name.lower()+"_target"))+" m³/h","process_small")
            text(.525,mid+.04,self._number(r["flow"])+" m³/h","process_bold",color=r["color"] if active else "#6D7E8C")
            text(.627,mid+.04,self._number(r["current_ma"],3)+" mA","process_small",color=r["color"])
            age="—" if r["age"] is None else str(r["age"])+" ms"
            if r["cached"]:age+=" ALT"
            text(.708,mid+.04,age,"process_small",color=r["color"])
        box(.82,.10,.985,.86)
        text(.835,.19,"Gesamt","process_bold","w")
        target=reg.get("target");error=total-target if total is not None and isinstance(target,(int,float)) else None
        for y,label,value in ((.38,"Istwert ALT" if stale else "Istwert",total),(.56,"Sollwert",target),(.74,"Abweichung",error)):
            text(.833,y,label,"process_small","w")
            value_color="#986000" if stale and y==.38 else "#142D3B"
            if label=="Abweichung" and isinstance(value,(int,float)) and math.isfinite(value):
                value_color="#167450" if getattr(self,"_total_trustworthy",False) and abs(value)<=20.0 else "#986000"
            text(.975,y,self._number(value)+" m³/h","process_small","e",color=value_color)
        text(.515,.95,"Grundlast GHF · Feinkorrektur GLF" if self._compact else "GHF hält die Grundlast · GLF korrigiert kleine Abweichungen","process_small")

    def _render_settings(self,snapshot):
        settings=snapshot.get("settings",{}) or {}
        pid=settings.get("pid",settings.get("PID",{})) or {}
        defaults={"GHF":{"kc":.9,"ti_min":.108,"td_min":.007},"GLF":{"kc":.32,"ti_min":.075,"td_min":.001}}
        lines=["Build: "+str(settings.get("build","Testversion 2"))+" · PID Ti/Td in Minuten (intern in Sekunden)"]
        for name in ("GLF","GHF"):
            p=pid.get(name,defaults[name])
            lines.append(f"{name}: Kc {self._number(p.get('kc'),3)} · Ti {self._number(p.get('ti_min'),3)} min · Td {self._number(p.get('td_min'),3)} min")
        lines.append("Diese Testversion gibt unter keinen Umständen reale Stellbefehle aus.")
        lines.append("Gesamtbereich: "+str(settings.get("total_range","92–549 m³/h, gewünschter Testbereich"))+" · Toleranz: "+str(settings.get("tolerance","±20 m³/h, vorläufig")))
        lines.append("FU-Faktor 1,05 nur im Modell · "+str(settings.get("curve","Echte Gesamt-FU-Kennlinie offen.")))
        lines.append(str(settings.get("legacy","Keine nachgewiesene 1:1-Umsetzung einer bisherigen Regelung.")))
        lines.append("Simulation: Stellwerte 0–100 % · Anhalten: letzte Befehle · Fehler/Schließen: 4-mA-Modellbefehle.")
        lines.append("Quelle: "+str(settings.get("source","Eigenständiger Teststand.")))
        self._settings_text.configure(text="\n".join(lines))
        channels=settings.get("channels",{}) or {}
        if channels:
            text="Kanalzuordnung: " + " · ".join(f"{k}: {v}" for k,v in channels.items())
        else:text="Hardwarezugriff: ausschließlich analoge Eingänge. Ausgangskanäle werden nicht geöffnet."
        self._channels.configure(text=text,wraplength=max(850,self.root.winfo_width()-60))

    def _manual_submit(self):
        try:
            values={k:float(v.get().strip().replace(",",".")) for k,v in self._manual_vars.items() if v.get().strip()}
            if not values:raise ValueError("Bitte mindestens einen Sollwert eingeben.")
            self.service.set_manual(values)
            for k in values:self._manual_vars[k].set("")
            self._manual_message.configure(text="Manuelle Sollwerte angefordert.",fg="#126B80")
            self._log("Manuelle Sollwerte: "+str(values))
        except Exception as exc:self._error("Manueller Stellbefehl",str(exc))

    def _manual_clear_inputs(self):
        for v in self._manual_vars.values():v.set("")
        self._manual_message.configure(text="Eingaben geleert · letzte Befehle unverändert.")

    def _reg_submit(self):
        try:
            target=float(self._target.get().strip().replace(",","."))
            self.service.start_regulation(self._mode.get(),target)
            self._log("Regelung angefordert: "+self._mode.get()+" / "+str(target)+" m³/h")
        except Exception as exc:self._error("Regelung nicht gestartet",str(exc))

    def _reg_stop_submit(self):
        try:self.service.stop_regulation();self._log("Regelung anhalten angefordert.")
        except Exception as exc:self._error("Regelung anhalten",str(exc))

    def _set_fault(self):
        try:self.service.set_fault("glf_closed",bool(self._fault_var.get()))
        except Exception as exc:self._error("Simulationseinstellung",str(exc))

    def _retry_hardware_start(self):
        try:
            self.service.retry_hardware()
            self._log("Messneuverbindung angefordert · Hardware bleibt ausschließlich lesend.")
        except Exception as exc:self._error("Messung neu verbinden",str(exc))

    def _log(self,text):
        import time
        self._events.append(time.strftime("%H:%M:%S")+" · "+text)
        self._log_text.configure(state="normal");self._log_text.delete("1.0","end")
        self._log_text.insert("end","\n\n".join(self._events));self._log_text.see("end");self._log_text.configure(state="disabled")

    def _error(self,title,message):
        self._log(title+": "+message)
        if self._dialog_active:return
        self._dialog_active=True
        try:self.messagebox.showerror(title,message+"\n\nMit OK quittieren. Danach Einstellungen korrigieren und erneut versuchen.",parent=self.root)
        finally:self._dialog_active=False

    def _show_service_errors(self,snapshot):
        if self._dialog_active:return
        fresh=[]
        for error in snapshot.get("errors",[]) or []:
            if not isinstance(error,dict):continue
            title=str(error.get("title","Fehler"));message=str(error.get("message",error.get("error","")))
            key=str(error.get("id",title+":"+message))
            if key in self._seen_errors:continue
            self._seen_errors.add(key);self._seen_order.append(key);fresh.append((title,message))
            while len(self._seen_order)>256:self._seen_errors.discard(self._seen_order.popleft())
        if fresh:
            self.root.update_idletasks()
            self._error(fresh[0][0] if len(fresh)==1 else "Aktuelle Fehlermeldungen","\n\n".join(t+"\n"+m for t,m in fresh))

    def _resize(self,event=None):
        if event is not None and event.widget is not self.root:return
        compact=self.root.winfo_width()<1200 or self.root.winfo_height()<740
        if compact==self._compact:return
        self._compact=compact
        for name,size in {"brand":14 if compact else 16,"title":14 if compact else 16,"heading":11 if compact else 12,"body":10,"bold":10,"small":9,"value":24 if compact else 28,"entry":12 if compact else 13}.items():self.fonts[name].configure(size=size)
        self.fonts["axis"].configure(size=-10 if compact else -11)
        self.fonts["badge"].configure(size=-11 if compact else -12)
        if hasattr(self,"_log_text"):self._log_text.configure(height=7 if compact else 10)

    def _poll(self):
        import time,traceback
        self._after=None
        if self._closing:return
        delay=100
        try:
            snapshot=self.service.snapshot()
            self._render(snapshot,time.monotonic())
            self._show_service_errors(snapshot)
        except Exception as exc:
            delay=500
            detail=type(exc).__name__+": "+str(exc)
            key="gui:"+detail
            if key not in self._seen_errors:
                self._seen_errors.add(key);traceback.print_exc()
                try:self._error("Oberfläche / Datenübernahme",detail)
                except Exception:traceback.print_exc()
        finally:
            if not self._closing:
                try:self._after=self.root.after(delay,self._poll)
                except self.tk.TclError:pass

    def _on_close(self):
        if self._closing:return
        self._closing=True
        if self._after is not None:
            try:self.root.after_cancel(self._after)
            except self.tk.TclError:pass
        try:self.service.close()
        except Exception as exc:
            self._closing=False;self._error("Programmabschluss",str(exc));self._poll();return
        self.root.destroy()



def run_self_tests():
    """Offline-Pruefungen ohne Tk-Fenster, NI-Treiber oder reale Ausgaenge."""
    import unittest

    class OfflineTests(unittest.TestCase):
        def test_electrical_endpoints(self):
            for name in ("FU", "GLF", "GHF"):
                expected = 0.0 if name == "FU" else 100.0
                self.assertAlmostEqual(percent_to_current(name, expected), 0.004)
                self.assertAlmostEqual(percent_to_current(name, 100 - expected), 0.020)

        def test_nonfinite_outputs_rejected(self):
            for value in (float("nan"), float("inf"), -1, 101, True):
                with self.assertRaises(ValueError):
                    percent_to_current("FU", value)

        def test_sensor_scaling_and_independent_validity(self):
            sample = FlowSample(10.0, 0.020, 0.030)
            self.assertAlmostEqual(sample.flows()["GHF"], 0.016 / 0.0000356)
            self.assertTrue(sample.valid("GHF", 10.2))
            self.assertFalse(sample.valid("GLF", 10.2))
            self.assertIsNotNone(sample.flows()["GLF"])

        def test_stale_and_future_samples_rejected(self):
            sample = FlowSample(10.0, .010, .010)
            self.assertFalse(sample.valid("GHF", 11.1))
            self.assertFalse(sample.valid("GHF", 9.9))

        def test_required_sensor_fault_resets_model_outputs(self):
            reg = AirflowRegulator()
            reg.start(RegulatorConfig(mode="BOTH", target=300), FlowSample(10.0, .010, .010), 10.0)
            commands, state = reg.update(FlowSample(10.1, .010, .030), 10.1)
            self.assertFalse(state["active"])
            self.assertEqual(commands, {"FU": 0.0, "GHF": 100.0, "GLF": 100.0})

        def test_intentional_stop_holds_commands(self):
            reg = AirflowRegulator()
            reg.start(RegulatorConfig(mode="GHF", target=160), FlowSample(10.0, .010, .010), 10.0)
            reg.update(FlowSample(10.1, .010, .010), 10.1)
            before = reg.commands
            reg.stop()
            after, state = reg.update(FlowSample(11.0, None, None), 11.0)
            self.assertFalse(state["active"])
            self.assertEqual(before, after)

        def test_unselected_sensor_does_not_block_other_branch(self):
            reg = AirflowRegulator()
            reg.start(RegulatorConfig(mode="GHF", target=160), FlowSample(10.0, .010, None), 10.0)
            commands, state = reg.update(FlowSample(10.1, .010, None), 10.1)
            self.assertTrue(state["active"])
            self.assertEqual(commands["GLF"], 0.0)
            self.assertGreaterEqual(commands["GHF"], 20.0)

        def test_target_limits_are_mode_specific(self):
            for mode, target in (("BOTH", 91.0), ("BOTH", 550.0), ("GLF", 101.0), ("GHF", 450.0)):
                with self.assertRaises(ValueError):
                    RegulatorConfig(mode=mode, target=target).validate()

        def test_small_retarget_preserves_baseload(self):
            reg = AirflowRegulator()
            reg.start(RegulatorConfig(mode="BOTH", target=300), FlowSample(10.0, .012, .012), 10.0)
            before = reg.base
            reg.retarget(305)
            self.assertEqual(before, reg.base)

        def test_combined_closed_loop_stays_within_tolerance(self):
            # Reale Hardware wird hier NICHT nachgebildet oder angesprochen.
            # Regression fuer den Koordinator: GLF-Vollaussteuerung darf nicht
            # unbemerkt zu einem bleibenden Gesamtfehler im Testmodell fuehren.
            for target in (92.0, 150.0, 250.0, 300.0):
                with self.subTest(target=target):
                    plant = SimulatedPlant(now=100.0)
                    reg = AirflowRegulator()
                    reg.start(RegulatorConfig(mode="BOTH", target=target), plant.sample(), plant.time, plant.commands)
                    tail = []
                    for step in range(1, 2401):
                        sample = plant.step(0.05, reg.commands)
                        commands, state = reg.update(sample, plant.time)
                        self.assertTrue(state["active"])
                        if step > 2000:
                            tail.append(abs(sum(sample.flows().values()) - target))
                    self.assertLessEqual(max(tail), 20.0)
                    if target == 300.0:
                        self.assertLess(abs(sum(sample.flows().values()) - target), 2.0)

        def test_tolerance_status_requires_fresh_finite_feedback(self):
            state = {"active": True, "state": "running", "target": 300.0}
            for actual in (280.0, 300.0, 320.0):
                self.assertTrue(AirflowApp._regulation_indicator(state, actual, True, False)[0].startswith("Innerhalb"))
            for actual in (279.99, 320.01):
                self.assertTrue(AirflowApp._regulation_indicator(state, actual, True, False)[0].startswith("Außerhalb"))
            for actual, trustworthy in ((float("nan"), True), (300.0, False)):
                self.assertFalse(AirflowApp._regulation_indicator(state, actual, trustworthy, False)[0].startswith("Innerhalb"))

        def test_service_initial_sample_is_not_in_future(self):
            service = AirflowService(autostart=False)
            try:
                self.assertTrue(service._sample.valid("GHF", time.monotonic()))
                service.start_regulation("BOTH", 300)
                self.assertTrue(service.snapshot()["regulation"]["active"])
            finally:
                service.close()

        def test_hardware_readonly_cannot_accept_commands(self):
            service = AirflowService("hardware_readonly", autostart=False)
            try:
                with self.assertRaises(RuntimeError):
                    service.set_manual({"FU": 20})
                with self.assertRaises(RuntimeError):
                    service.start_regulation("BOTH", 300)
                self.assertIsNone(service.snapshot()["outputs"]["FU"]["percent"])
            finally:
                service.close()

        def test_manual_recovery_after_simulated_fault(self):
            service = AirflowService(autostart=False)
            try:
                service._regulator.fault("Testfehler")
                service.set_manual({"FU": 25, "GLF": 40, "GHF": 60})
                snap = service.snapshot()
                self.assertFalse(snap["regulation"]["active"])
                self.assertEqual(snap["outputs"]["FU"]["percent"], 25)
                self.assertEqual(snap["regulation"]["fu_percent"], 25)
            finally:
                service.close()

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(OfflineTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1




def main(argv=None):
    import argparse
    import sys
    parser = argparse.ArgumentParser(
        description="Nassheuer-Luftsteuerung TESTVERSION: Simulation oder Hardware NUR LESEN. Keine realen Ausgangsschreibzugriffe.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--simulate", action="store_true", help="Simulation starten (Standard).")
    mode.add_argument("--hardware-readonly", action="store_true", help="Optional NI-Sensoren lesen; keine Ausgangsbefehle. Vorhandene reale Ausgangswerte werden nicht veraendert.")
    parser.add_argument("--self-test", action="store_true", help="Reine Offline-Pruefungen ausfuehren, ohne Fenster oder NI-Zugriff.")
    parser.add_argument("--version", action="version", version=TEST_BUILD)
    parser.add_argument("--geometry", default="1366x768", help="Fenstergroesse, z.B. 1366x768.")
    parser.add_argument("--gui-smoke-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.self_test:
        return run_self_tests()
    try:
        import tkinter as tk
        root = tk.Tk()
    except Exception as exc:
        print("Grafische Oberflaeche konnte nicht gestartet werden: " + str(exc), file=sys.stderr)
        print("Unter Windows eine Python-Installation mit Tcl/Tk verwenden. Offline-Pruefung: --self-test", file=sys.stderr)
        return 2
    service = AirflowService("hardware_readonly" if args.hardware_readonly else "simulation")
    failures = []

    def report_callback_exception(kind, value, traceback):
        import traceback as tb
        failures.append(str(value))
        tb.print_exception(kind, value, traceback)
        if args.gui_smoke_test:
            root.after(0, root.destroy)

    root.report_callback_exception = report_callback_exception
    try:
        app = AirflowApp(root, service)
        root.geometry(args.geometry)
        if args.gui_smoke_test:
            root.after(1800, root.destroy)
        root.mainloop()
    finally:
        service.close()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
