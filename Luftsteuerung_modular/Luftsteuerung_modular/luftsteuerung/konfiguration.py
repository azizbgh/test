"""Geräteadressen, Kanalreihenfolge, Messfaktoren und Zeitgrenzen.

Hier stehen die übernommenen Anlagenwerte. Diese Datei rechnet nicht und
öffnet keine NI-Verbindung. Kennlinien, Hardwarezugriff, Messwertbewertung,
Worker und Oberfläche importieren jeweils die benötigten Werte.

Einheiten: A = Ampere, mA = Milliampere, m³/h = Kubikmeter pro Stunde.
0.004 A entsprechen 4 mA. Die Zuordnungen und Zahlen sind unverändert.
Die hier genannten Module und Verdrahtungen wurden vor Ort nicht überprüft.
"""

from __future__ import annotations




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
    "FU": "cDAQ9183-251EC5DMod1/ao0",
    "GLF": "cDAQ9183-251EC5DMod1/ao1",
    "GHF": "cDAQ9183-251EC5DMod1/ao2",
}
# Eingangskanäle haben einen eigenen Vertrag: Rückgabe stets (GHF/ai0, GLF/ai1).
AI_GHF = "cDAQ9183-251EC5DMod2/ai0"
AI_GLF = "cDAQ9183-251EC5DMod2/ai1"
# Reihenfolge stimmt mit dem gemeinsamen NI-Mess-Task überein. Die Bewertung
# und Darstellung erfolgen trotzdem unabhängig für jede einzelne Messstelle.
SENSOR_CHANNELS = {"GHF": AI_GHF, "GLF": AI_GLF}

# Verschiedene Begriffe nicht vermischen: DAQ-Konfigurationsbereich,
# erwarteter Sensorbereich, Stellgliedkennlinie und tatsächlicher Anlagenzustand.
# Die Konstanten sind übernommene Vorgaben, keine neu bestätigten Modulgrenzen.
MIN_OUTPUT_A = 0.0
MAX_OUTPUT_A = 0.020              # NI-9266: 0–20 mA
SENSOR_ZERO_A = 0.004            # Sensor-Bezugspunkt: 4 mA
SENSOR_MAX_A = 0.020           # Erwartetes oberes Sensorsignal
SENSOR_VALID_MIN_A = 0.0035    # +-0,5mA Toleranz
SENSOR_VALID_MAX_A = 0.0205    # +-0,5mA Toleranz

# Dimensionsprüfung: (A - A) / (A je m³/h) ergibt m³/h.
# Sensorfaktoren dürfen keine Aktuatorkennlinie allein aufgrund gleicher Einheit ersetzen.
GHF_SENSOR_FACTOR = 0.0000356   # A je m³/h; vorhandene Messformel
GLF_SENSOR_FACTOR = 0.00016     # A je m³/h; vorhandene Messformel
GHF_REFERENCE_M3H = 449.0       # Bezugswert der alten Durchfluss-Prozentfunktion
GLF_REFERENCE_M3H = 100.0       # Bezugswert der alten Durchfluss-Prozentfunktion

# Erfassung und Anzeige besitzen verschiedene Takte: kontinuierliche Samples im
# Task, gemittelte Pakete für die GUI. Frischegrenze ist nur Software-Plausibilität.
SAMPLE_RATE_HZ = 1000           # NI-9253: 1000 Samples/s und Kanal
GUI_REFRESH_MS = 50            # GUI wird ca. 20-mal/s aktualisiert
MAX_MEASUREMENT_AGE_S = 1.0    # Nur Software-Plausibilität, keine Safety-Funktion
