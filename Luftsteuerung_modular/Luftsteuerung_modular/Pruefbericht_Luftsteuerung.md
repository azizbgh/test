# Prüfbericht – Modularisierung der Luftsteuerung

Stand: 17. November 2026.

## Grundlage und Umfang

Vollständig gelesene Quelle: `Texte collé(8).txt`, 3,400 Textzeilen, 136,164 Bytes. Die Quelle enthält 17 Funktionen und sieben Klassen auf Modulebene. Die neue Aufteilung enthält diese 24 Definitionen vollständig und zusätzlich genau eine interne Hilfsfunktion zum gemeinsamen GLF-/GHF-Rechenweg. Zwei `__init__.py`-Dateien kennzeichnen die Pakete und starten nichts.

Die ursprüngliche .txt-Datei bleibt erhalten. Die Sicherung im ZIP stimmt bytegenau mit dem hochgeladenen Anhang überein, einschließlich seiner ursprünglichen Zeilenenden.

SHA-256 der Quelle und Sicherung:

```text
a95962fad725506db98a3f67fccec0a3666a0cdf59a38dddbd1eca92bcce1df4
```

## A – Reine Codeverschiebung

Die folgenden Zuordnungen beziehen sich auf Zeilennummern des hochgeladenen Originals. Die Struktur ausführbarer Anweisungen wurde mit Pythons abstraktem Syntaxbaum verglichen. Kommentare, Dokumentationsstrings und durch die Verschiebung veränderte Zeilennummern werden bei diesem Vergleich nicht als Logikänderung gezählt.

| Ursprünglicher Bereich | Ursprüngliche Zeilen | Zieldatei | Ergebnis |
|---|---:|---|---|
| Geräte- und Messkonfiguration | 50–86 | `luftsteuerung/konfiguration.py` | Alle Werte und Zuordnungen unverändert |
| Prüffunktionen und Umrechnungen | 96–238 | `luftsteuerung/kennlinien.py` | Unverändert, außer den zwei ausdrücklich unter B genannten Delegationen |
| Kennlinien-Wörterbuch und Registrierung | 92, 244–246 | `luftsteuerung/kennlinien.py` | Ein gemeinsames Objekt, gleiche Registrierung und Reihenfolge |
| DaqController | 250–512 | `luftsteuerung/hardware.py` | Ausführbare Codestruktur identisch |
| SimulatedDaqController | 516–564 | `luftsteuerung/hardware.py` | Ausführbare Codestruktur identisch |
| SensorReading und evaluate_sensor_currents | 567–615 | `luftsteuerung/messwerte.py` | Ausführbare Codestruktur identisch, Dekorator erhalten |
| DaqState | 621–626 | `luftsteuerung/messwerte.py` | Datenfelder und Dekorator identisch |
| DaqWorker | 630–893 | `luftsteuerung/worker.py` | Ausführbare Codestruktur identisch |
| Farbwerte und number_de | 902–915 | `luftsteuerung/oberflaeche/darstellung.py` | Werte und Formatierung identisch |
| LuftsteuerungApp | 919–1963 | `luftsteuerung/oberflaeche/basis.py` | Sämtliche ausführbaren Methoden identisch |
| LuftsteuerungDashboard | 1973–3386 | `luftsteuerung/oberflaeche/dashboard.py` | Sämtliche ausführbaren Methoden identisch |
| main und Startschutz | 3389–3400 | `main.py` | Argumente, Standardmodus und gestartete Klasse identisch |

22 der 24 ursprünglichen Funktions-/Klassendefinitionen sind nach diesem Vergleich unverändert. Alle ursprünglichen Zuweisungen auf Modulebene, einschließlich der Kennlinienregistrierung und Farben, sind erhalten und strukturell identisch. Die neue globale Aufteilung verändert deren Modulzugehörigkeit.

## B – Tatsächlich redundante Rechnung

Vorher enthielten `glf_percent_to_amp` und `ghf_percent_to_amp` denselben Rechenweg mit unterschiedlichen Kanalnamen für die Eingabeprüfung. Jetzt ruft jede Funktion `_gleitschieber_percent_to_amp(percent, name)` auf. Diese Hilfsfunktion prüft zuerst den Prozentwert, führt dieselbe Formel in derselben Reihenfolge aus und prüft anschließend denselben Strombereich. Die Schnittstellen der bisherigen Funktionen bleiben erhalten.

Die Originalrechnung lautet weiterhin:

```python
current_a = MAX_OUTPUT_A - percent / 100 * (
    MAX_OUTPUT_A - SENSOR_ZERO_A
)
```

Keine Rundung, Umstellung der Rechenoperationen oder Gleichsetzung mit einer Sensorformel wurde vorgenommen. Der zusätzliche Hilfsaufruf verändert naturgemäß die interne Aufrufkette und gegebenenfalls einen Traceback. Ergebnisse, Fehlertypen und Meldungstexte wurden dagegen verglichen. Exakte Laufzeitgleichheit wird nicht behauptet.

Bewusst erhalten sind die Überschreibungen in der Dashboardklasse, die ursprünglichen Bezugsfunktionen und vorhandene GUI-Erzeugungs-/Bindungsaufrufe. Beispielsweise weist `_build_control_tab` der Basisansicht `self.log_box` zweimal zu und registriert die Größenbindung mehrfach. Dies sind beobachtete Seiteneffekte des Originalcodes, keine gefahrlos löschbaren reinen Rechenduplikate. Ihre Entfernung würde eine gesonderte Verhaltensänderung erfordern.

## C – Imports und gemeinsam genutzter Zustand

- Explizite relative Imports innerhalb des Pakets ersetzen die gemeinsame globale Namensumgebung der Einzeldatei. `main.py` verwendet den Paketimport auf die Dashboardklasse. Es gibt keine Sternimports und keine Änderung von `sys.path` im ausgelieferten Programm.
- `CONFIGURED_ACTUATOR_CURVES` wird genau einmal in `kennlinien.py` definiert und dort nach Definition der Funktionen registriert. Controller, Worker und Basisoberfläche referenzieren nachweislich dasselbe Wörterbuchobjekt. Es wird keine Kopie angelegt.
- Geräte- und Kanalwörterbücher sowie Sensorreihenfolge kommen aus `konfiguration.py`; sie werden nicht in mehreren Dateien neu definiert.
- Die ursprünglich am Ende der Basisoberfläche stehenden Tk-/Schrift-/Deque-Imports stehen in denjenigen GUI-Modulen, die sie benötigen. Das nachgeladene `nidaqmx` bleibt nachgeladen. Die Simulation lädt es nicht.
- Die vorhandenen Instanzzustände, Queues, Sperren, Ereignisse, Threadparameter und die Vererbung bleiben erhalten. Die Task-Erzeugung beginnt nicht bereits beim Modulimport.
- Der vorhandene Ausdruck `__file__` im Protokoll zeigt nach der Verschiebung auf `basis.py`. Der Start erfolgt im neuen Ordner über `main.py`. Klassen und Funktionen haben aufgrund der Modularisierung neue Modulpfade; externe Importaufrufer außerhalb des Anhangs konnten nicht geprüft werden.

## Dokumentationswidersprüche im Original

| Beobachtung im Original | Maßgeblicher ausgeführter Code | Umgang damit |
|---|---|---|
| Kopftext behauptet GLF/GHF 0–100 % = 0–20 mA | Beide Funktionen rechnen umgekehrt von 20 auf 4 mA; die aktive UI zeigt dies bereits so an | Formel und UI erhalten, Kommentar berichtigt |
| Kopftext behauptet keine Ausgaben beim Start/Schließen | DaqWorker.run schreibt beim Start dreimal 0.004 A; DaqController.close_tasks fordert vor der Freigabe ebenfalls dreimal 0.004 A an | Steuerbefehle erhalten, neue Modulbeschreibung erläutert das Verhalten |
| Controller-Docstring behauptet, niemals selbst einen Abschlussstrom auszugeben | close_tasks enthält ausdrücklich Schreibaufrufe, auch für zuvor unbenutzte Ausgänge | Ausschließlich Dokumentation berichtigt |
| Einzelne Altkommentare sprechen von gemeinsamer Sensorfreigabe und dauerhafter Verriegelung | Der manuelle Ausgabeweg warnt vor Sensorfehlern, lässt bestätigte Aufträge aber zu; Ausgangsfehler werden pro Kanal behandelt | Ausführbaren Code erhalten; keine neue Verriegelung eingebaut |

Das sind Befunde aus dem Programmtext. Daraus folgt keine Aussage darüber, ob Hardwarekennlinien oder Anlagenzuordnungen physisch richtig sind. Auch eine fachliche Bestätigung durch frühere Kommentare wird nicht erneut behauptet.

## Durchgeführte Prüfungen

Verifikation mit Python 3.12.14. Ergebnis: **15 Tests bestanden, 0 Fehlschläge, 0 Fehler**. Die Testfunktionen enthalten zahlreiche Unterfälle und Vergleichsaufrufe; „15 Tests“ bedeutet nicht nur 15 einzelne Eingaben.

| Prüfung | Umfang | Ergebnis |
|---|---|---|
| Original und Sicherung | Bytevergleich einschließlich Zeilenenden | Identisch |
| Syntax | Alle elf Python-Dateien kompiliert, ohne sie als Anwendung zu starten | Bestanden |
| Vollständigkeit | Alle 24 alten Definitionen und alle ursprünglichen globalen Zuweisungen nachverfolgt | Vollständig |
| Strukturvergleich | 22 unveränderte Definitionen; genau zwei Delegationen; genau eine neue Hilfsdefinition | Bestanden |
| Imports | Referenzierte globale Namen, vorhandene Zielmodule und azyklische Importbeziehungen | Bestanden |
| Reine Module ohne GUI/NI | Separater Prozess blockiert tkinter, customtkinter und nidaqmx und importiert die sechs reinen Module | Bestanden |
| Prozent-/Kennlinienfunktionen | 0–100 % in 0,1-%-Schritten sowie ungültige Werte; FU, GLF, GHF und Bezugsfunktionen | Exakte Fließkommaergebnisse und Fehler identisch |
| Strom-/Sensorformeln | Werte von −0,001 bis 0,022 A in 0,00001-A-Schritten sowie ungültige Datentypen, NaN und Unendlichkeit | Ergebnisse und Fehler identisch |
| Texteingaben und Zahlenformat | Leere Felder, Leerzeichen, Komma, Exponentialdarstellung, ungültiger Text und Grenzverletzungen | Identisch |
| Kennlinienregister und Aufträge | Objektidentität zwischen Modulen, Kanalauswahl, Reihenfolge, fehlende Kennlinie und Fehlermeldungen | Identisch |
| Sensorbewertung | Einzelne fehlende/ungültige Kanäle, Grenzen, NaN/Unendlichkeit und Altersgrenze | Identisch |
| Echter Controller mit nachgebildetem Treiber | 15 Szenarien einschließlich Konfiguration, Lesepuffer, Schreibfehler, Teilfehler, Wiederholungen und Abschlussfehler | Aufrufreihenfolgen, Parameter, Meldungen und Zustände identisch |
| Simulation | Gleicher fester Zeitbezug, Beispieldaten, Sollwertspeicherung und Freigabe | Identisch; kein nidaqmx-Import |
| Ausgewählte GUI-Methoden | Widget-Nachbildungen für Vorschau, Übernahmebestätigung, Leeren, Zustandswechsel und Sollwertpfeile | Identisch |
| Programmeinstieg | Kein Parameter, --simulate, --hardware und widersprüchliche Parameter; Dashboardstart durch Nachbildung ersetzt | Gleiche Moduswahl und Argumentfehler |
| Worker direkt | Lesen und Schreiben mit nachgebildetem Treiber | Gleiche Ereignisse und Zustände |
| Worker als tatsächlicher Thread | Startwerte, manueller Auftrag, zweiter abgewiesener Auftrag, Messneustart, Abschluss und gesperrte Folgeaufträge | Bestanden für Original und neue Aufteilung |

Die Tests haben kein reales NI-Gerät geöffnet und kein sichtbares Fenster erzeugt. Die Dummy-Klasse für den Import des GUI-Moduls und die Treiber-/Widget-Nachbildungen existieren ausschließlich in der separaten Verifikationsumgebung; sie sind kein Bestandteil der ausgelieferten Programmdateien.

## Verifikationsprotokoll

```text
test_command_order_failures_and_shared_registry (__main__.RefactorTests.test_command_order_failures_and_shared_registry) ... ok
test_current_validation_and_sensor_formulas (__main__.RefactorTests.test_current_validation_and_sensor_formulas) ... ok
test_daq_call_sequences_and_failure_paths (__main__.RefactorTests.test_daq_call_sequences_and_failure_paths) ... ok
test_dashboard_setpoint_arrows (__main__.RefactorTests.test_dashboard_setpoint_arrows) ... ok
test_entrypoint_default_simulation_and_conflict (__main__.RefactorTests.test_entrypoint_default_simulation_and_conflict) ... ok
test_headless_core_imports_without_gui_or_ni (__main__.RefactorTests.test_headless_core_imports_without_gui_or_ni) ... ok
test_original_backup_bytes (__main__.RefactorTests.test_original_backup_bytes) ... ok
test_parse_and_number_format (__main__.RefactorTests.test_parse_and_number_format) ... ok
test_percent_validation_and_curves_exact_floats (__main__.RefactorTests.test_percent_validation_and_curves_exact_floats) ... ok
test_selected_gui_handlers_with_widget_doubles (__main__.RefactorTests.test_selected_gui_handlers_with_widget_doubles) ... ok
test_sensor_states_and_freshness (__main__.RefactorTests.test_sensor_states_and_freshness) ... ok
test_simulation_exact_values (__main__.RefactorTests.test_simulation_exact_values) ... ok
test_syntax_definitions_constants_and_import_graph (__main__.RefactorTests.test_syntax_definitions_constants_and_import_graph) ... ok
test_worker_read_write_and_partial_errors (__main__.RefactorTests.test_worker_read_write_and_partial_errors) ... ok
test_worker_thread_start_write_restart_close (__main__.RefactorTests.test_worker_thread_start_write_restart_close) ... ok

----------------------------------------------------------------------
Ran 15 tests in 0.464s

OK
```

## Nicht geprüft

- Sichtbares Fenster, Rendering, DPI-Skalierung und Dialoge mit einer realen CustomTkinter-Installation unter Windows.
- Vorhandensein/Funktion der realen NI-Treiber, Module, Versorgung, Verdrahtung oder tatsächlichen Ausgangsströme.
- Nicht mitgelieferte externe Skripte, die alte Modulpfade importieren oder globale Namen der früheren Einzeldatei von außen ersetzen.

Die Vergleichsergebnisse stützen die Erhaltung des untersuchten Verhaltens. Sie sind keine uneingeschränkte Garantie für jeden möglichen Fehlerpfad oder die reale Anlage. Es wurden keine vorhandenen Fehler eigenmächtig behoben und keine zusätzlichen Bedienfunktionen oder neuen externen Abhängigkeiten eingebaut.
