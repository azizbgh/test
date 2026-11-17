# Nassheuer-Luftsteuerung – Testversion

[**Vollständige ZIP-Datei herunterladen**](https://github.com/azizbgh/test/raw/refs/heads/main/Luftsteuerung_Testversion.zip)

Das ZIP enthält genau **eine Python-Datei**: `luftsteuerung_testversion.py`.
Entpacken und mit Python starten:

```powershell
python luftsteuerung_testversion.py
```

Für die Simulation werden keine zusätzlichen Python-Pakete benötigt; eine
Python-Installation mit Tkinter genügt. Die Oberfläche enthält **Steuerung,
Regelung und Einstellungen**, Messwertalter in Millisekunden und Diagramme.

## Umfang dieser Version

Dies ist ein **eigenständig aufgebauter Teststand**. Der letzte im Chat
hochgeladene Programmstand war beim Erstellen nicht erneut abrufbar. Diese
Datei ist daher keine nachgewiesene vollständige Übernahme dieses Standes.

- Standardmäßig werden ausschließlich ein künstliches Anlagenmodell und
  simulierte Stellbefehle verwendet.
- GHF-Grundlast und GLF-Feinkorrektur können gemeinsam oder einzeln erprobt
  werden. Die PID-Parameter sind übernommen; die PID-Berechnung ist eine
  dokumentierte eigene Implementierung, keine nachgewiesene LabVIEW-Kopie.
- Gewünschter Gesamtbereich: **92–549 m³/h**, vorläufige Toleranz **±20 m³/h**.
  Das sind Anforderungen, keine nachgewiesenen Leistungsdaten der Anlage.
- Bewusster Regler-Stopp hält die letzten **Simulationsbefehle**.
- Regelfehler setzen im Modell FU auf 0 %, GLF und GHF auf 100 %,
  entsprechend jeweils 4 mA. Das bedeutet keinen nachgewiesenen Stillstand.
- Die FU-Kennlinie des Modells ist **künstlich**. Eine reale Gesamtkennlinie
  wurde damit nicht ermittelt oder bestätigt.

## Optionale Hardwaremessung: ausschließlich lesen

```powershell
python luftsteuerung_testversion.py --hardware-readonly
```

Dafür werden der NI-DAQmx-Treiber und das Python-Paket `nidaqmx` benötigt.
Dieser Modus liest nur `Mod2/ai0` und `Mod2/ai1` des konfigurierten Geräts.
**Es existieren keine NI-Analogausgangstasks und keine NI-Ausgangsschreibaufrufe.**
Vorhandene reale Ausgangswerte werden beim Starten, bei Fehlern und beim
Schließen **nicht geändert**. Insbesondere wird in diesem Modus keine reale
4-mA-Ausgabe ausgeführt. Dieser Teststand ersetzt noch keine reale
LabVIEW-Regelung.

## Offline-Prüfung

```powershell
python luftsteuerung_testversion.py --self-test
```

Die eingebauten Prüfungen benötigen weder eine grafische Umgebung noch einen
NI-Treiber. Zusätzlich wurden Regler-/Datendienstprüfungen und die tatsächliche
Tk-Oberfläche unter Linux bei 1024×700, 1366×768 und 1536×864 geprüft.
Es wurden keine reale Hardware und keine Windows-Installation getestet.

Die bereits vorhandenen Dateien `luftsteuerung_kommentiert.py` und
`Luftsteuerung_Hardware_Paket.zip` sind ältere Versionen und bleiben separat.
