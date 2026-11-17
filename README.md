# Nassheuer-Luftsteuerung – Testversion

[**Vollständige ZIP-Datei herunterladen**](https://github.com/azizbgh/test/raw/refs/heads/main/Luftsteuerung_Testversion.zip)

Das ZIP enthält genau **eine Python-Datei**: `luftsteuerung_testversion.py`.
Entpacken und mit Python starten:

```powershell
python luftsteuerung_testversion.py
```

## Testversion 2: Korrektur der gemeinsamen Regelung

Die Nachführung berücksichtigt jetzt die Stellgrenzen des GLF-Ventils und
den noch offenen Gesamtfehler. Die vorherige Version konnte die GHF-Grundlast
zu früh festhalten, obwohl GLF bereits vollständig geöffnet war. Besonders
bei 300 m³/h blieb dadurch im Testmodell eine erhebliche Abweichung stehen.
Das GLF-Arbeitsband 40–60 m³/h hat gegenüber fehlender Stellreserve keinen
Vorrang mehr. PID-Parameter, Messwertanzeige und Toleranz wurden nicht geändert.

Fenstertitel und Einstellungen zeigen **Testversion 2**. Der Regelstatus
unterscheidet jetzt ausdrücklich zwischen innerhalb und außerhalb von ±20 m³/h;
alte oder ungültige Rückmeldungen werden nicht als erreicht markiert.
Für die neue Datei das alte Programmfenster schließen, das ZIP erneut entpacken
und die daraus entnommene Python-Datei starten.

Im unveränderten synthetischen Modell mit 50-ms-Schritten liegt der Istwert
bei Sollwert 300 nach 30 s Modellzeit bei 293,304 und nach 60 s bei
303,613 m³/h. Ab etwa 16 s bleibt dieser Test innerhalb ±20 m³/h.
Zusätzlich werden mehrere Sollwerte von 92 bis 549, schwankende Zeitschritte,
kleine Sollwertänderungen und große Sollwertwechsel geprüft. Das ist kein
Nachweis für das Verhalten der realen Anlage.

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
