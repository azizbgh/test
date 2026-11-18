"""Gemeinsame Farben und deutsches Zahlenformat der beiden Ansichten.

Farben werden hier nur als Zeichenketten gespeichert. number_de verändert die
Anzeige einer Zahl, zum Beispiel 12.5 zu "12,50". Der Zahlenwert für Berechnungen
und Hardware bleibt unverändert. Diese Datei benötigt kein GUI-Framework.
"""

from __future__ import annotations




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
