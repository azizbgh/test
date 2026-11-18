"""Nassheuer-Luftsteuerung: zentraler Programmeinstieg.

Start im Projekt-Hauptordner: python main.py --simulate
Ohne Parameter wird ebenfalls die vorhandene Simulation verwendet.
--hardware wählt wie bisher die reale NI-Hardware. Dabei fordert der
vorhandene Worker beim Start 4 mA an allen drei Ausgängen an. Beim bestätigten
Schließen fordert der reale Controller ebenfalls dreimal 4 mA an.

Die älteren Kopfkommentare des Originals widersprachen diesem ausgeführten
Verhalten. Hier ist ausschließlich die Beschreibung angepasst. Kennlinien,
Kanäle, Oberfläche und Steuerabläufe sind nicht entsprechend umgebaut worden.
Ein bestätigter DAQ-Schreibaufruf ist keine Rückmessung des Ausgangsstroms.
"""

from __future__ import annotations

import argparse

from luftsteuerung.oberflaeche.dashboard import LuftsteuerungDashboard


# Argumente auswerten und dieselbe Dashboardklasse wie im Original starten.
# Ein Import dieser Datei ruft main() nicht auf.
def main():
    parser = argparse.ArgumentParser(description="Nassheuer-Luftsteuerung")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--simulate", action="store_true", help="Simulation ohne NI-Zugriff")
    group.add_argument("--hardware", action="store_true", help="Reale NI-Hardware verwenden")
    args = parser.parse_args()
    app = LuftsteuerungDashboard(simulate=not args.hardware)
    app.mainloop()


if __name__ == "__main__":
    main()
