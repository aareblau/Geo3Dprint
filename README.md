# Geo3Dprint

Geo3Dprint erstellt aus Schweizer Höhendaten ein 3D-druckbares
STL-Geländemodell. Das Programm nimmt einen Ort oder Koordinaten entgegen,
holt passende swissSURFACE3D-Rasterdaten von swisstopo, baut daraus ein
Höhenraster und schreibt am Ende eine geschlossene STL-Datei mit
topografischer Oberfläche, Seitenwänden und Bodenplatte.

Das Ergebnis heisst standardmässig `terrain.stl`.

## Quickstart

Der geführte Modus ist der Standard und wird für die maximale Kompatibilität
empfohlen:

```powershell
python .\programm.py
```

Der geführte Standardmodus fragt nur die wichtigsten Werte ab, nutzt feste,
druckfreundliche Einstellungen und erzeugt ein Modell in Millimetern mit einer
langen Seite von 10 cm. Nach dem Export wird die STL-Datei automatisch mit der
Standard-App für STL-Dateien geöffnet.

Für die maximale Detailgenauigkeit sollte der gewählte Ausschnitt unter
`1 km` pro Seite bleiben. Grössere Ausschnitte funktionieren, werden aber
automatisch grober gerastert und können sehr grosse STL-Dateien erzeugen.

## Voraussetzungen

- Windows: kein vorinstalliertes Python nötig, wenn `Geo3Dprint-starten.bat`
  verwendet wird
- Internetzugang beim ersten Start sowie für geo.admin und swisstopo
- Python 3.10 oder neuer nur bei manuellem Start über `python .\programm.py`
- Ein 3D-Viewer oder eine Slicer-Software zum Öffnen der fertigen STL-Datei,
  zum Beispiel Microsoft 3D Viewer:
  https://apps.microsoft.com/detail/9nblggh42ths?hl=de-DE&gl=US

## Installation

Unter Windows reicht ein Doppelklick auf:

```text
Geo3Dprint-starten.bat
```

Der Starter verwendet zuerst ein bereits vorhandenes Python. Nur wenn kein
nutzbares Python gefunden wird, lädt er automatisch eine lokale Python-Laufzeit
in den Projektordner. Danach erstellt er `.venv`, installiert die Abhängigkeiten
und startet den geführten Standardmodus. Auf dem Computer muss Python dafür
nicht vorher installiert sein; beim ersten Start ohne vorhandenes Python ist
aber Internetzugang nötig.

Wenn Python bereits installiert ist, kann Geo3Dprint auch manuell gestartet
werden. Im Projektordner zuerst die Python-Abhängigkeiten installieren:

```powershell
python -m pip install -r requirements.txt
```

Danach den geführten Standardmodus starten:

```powershell
python .\programm.py
```

Der Ordner `.runtime/` enthält nur die automatisch heruntergeladene lokale
Python-Laufzeit und wird nicht ins Git-Repository aufgenommen.

## Fertiges Modell anschauen

Die erzeugte STL-Datei kann mit jedem beliebigen 3D-Viewer oder jeder
Slicer-Software geöffnet werden. Ein solcher Viewer oder Slicer sollte vor dem
Export installiert sein, damit die Datei nach dem Export direkt geöffnet werden
kann.

Beispiele:

- Microsoft 3D Viewer:
  https://apps.microsoft.com/detail/9nblggh42ths?hl=de-DE&gl=US
- Windows-Dateizuordnung für `.stl`
- Cura, PrusaSlicer, Bambu Studio oder eine andere Slicer-Software
- MeshLab, Blender oder andere 3D-Viewer

Im geführten Standardmodus versucht Geo3Dprint nach dem Export, die STL-Datei
automatisch mit der Standard-App des Systems zu öffnen. Wenn das nicht klappt,
kann `terrain.stl` manuell im gewünschten Viewer oder Slicer geöffnet werden.

## Verwendung

### Geführter Standardmodus

```powershell
python .\programm.py
```

`programm.py` ist der empfohlene Startpunkt. Er startet den geführten
Assistenten mit reduzierten, druckfreundlichen Einstellungen.

Beim Start wird automatisch die Schweizer Karte geöffnet:

```text
https://map.geo.admin.ch/
```

In der Karte kann ein Ort gesucht werden. Mit Rechtsklick auf die Karte werden
Koordinaten angezeigt. Das oberste Koordinatenpaar kann kopiert und im Programm
eingefügt werden. Alternativ kann direkt ein Ortsname eingegeben werden, zum
Beispiel `Bern` oder `Matterhorn`.

Der geführte Standardmodus fragt:

1. Ort oder Koordinaten
2. Breite des Ausschnitts
3. Höhe des Ausschnitts

Akzeptierte Beispiele für den Ort:

```text
Bern
Matterhorn
2620760.50 1158843.35
2620760,50 1158843,35
2'620'760.50, 1'158'843.35
46.948 7.447
```

Akzeptierte Beispiele für Breite und Höhe:

```text
500
1000
1000m
1 km
0.5km
klein
mittel
gross
```

Die Breite und Höhe sind auf maximal `5000 m` pro Seite begrenzt. Für beste
Detailqualität sollte der Ausschnitt aber kleiner als `1000 m x 1000 m` sein.

Feste Einstellungen im geführten Standardmodus:

| Einstellung | Wert |
| --- | ---: |
| Ausgabedatei | `terrain.stl` |
| STL-Einheit | `mm` |
| Modellgrösse | lange Seite `100 mm` |
| Sockel im Modell | `3 mm` |
| Z-Verstärkung | `1.0` |
| Resampling | `bilinear` |
| Rechteck-Merge | aktiv |
| Wand-Merge | aktiv |

Die Rasterauflösung wird automatisch gewählt. Ein Ausschnitt bis
`1000 m x 1000 m` wird mit `0.5 m/Pixel` verarbeitet. Grössere Flächen werden
automatisch grober gerastert, damit Speicherbedarf, Laufzeit und STL-Grösse
beherrschbar bleiben.

| Ausschnitt | Automatische Auflösung |
| --- | ---: |
| `500 m x 500 m` | `0.5 m/Pixel` |
| `1000 m x 1000 m` | `0.5 m/Pixel` |
| `2000 m x 2000 m` | `1.0 m/Pixel` |
| `4000 m x 4000 m` | `2.0 m/Pixel` |

### Alternativer Assistenten-Startpunkt

```powershell
python .\programm_assistent.py
```

Dieser Startpunkt verwendet denselben Assistenten wie `programm.py`. Er bleibt
als neutral benannte Alternative erhalten.

### Erweiterter Modus

```powershell
python .\programm_advanced.py
```

Dieser Modus richtet sich an Nutzer, die alle technischen Einstellungen selbst
setzen wollen. Er fragt unter anderem:

- Ort oder Koordinaten
- Rasterauflösung
- Rechteckbreite und Rechteckhöhe
- Z-Verstärkung
- Sockelhöhe
- Massstab
- Resampling-Methode
- Ausgabedatei

Im erweiterten Modus gibt es kein fixes `5000 m x 5000 m`-Seitenlimit. Sehr
grosse Ausschnitte können aber lange laufen und sehr grosse STL-Dateien
erzeugen.

Standardwerte in `programm_advanced.py`:

| Einstellung | Standard |
| --- | ---: |
| Ausgabedatei | `terrain.stl` |
| Rasterauflösung | `0.5 m/Pixel` |
| Z-Verstärkung | `1.0` |
| Sockelhöhe | `50.0 m` |
| Rechteckbreite | `1000.0 m` |
| Rechteckhöhe | `1000.0 m` |
| Massstab | `1:10000` |
| Ausgabeeinheit | `m` |
| Resampling | `bilinear` |
| Rechteck-Merge | aktiv |
| Wand-Merge | aktiv |

### Nicht-interaktiver Start

Ohne Eingabedialog muss eine Bounding Box in Schweizer LV95-Koordinaten
übergeben werden:

```powershell
python .\programm_advanced.py --no-interactive --bbox MIN_E MIN_N MAX_E MAX_N
```

Beispiel:

```powershell
python .\programm_advanced.py --no-interactive --bbox 2620260.5 1158343.35 2621260.5 1159343.35
```

Dieses Beispiel erzeugt ein Modell um den Mittelpunkt
`E=2620760.50, N=1158843.35`.

## Wie Geo3Dprint arbeitet

1. Die Eingabe wird als Ortsname, LV95-Koordinate oder WGS84-Koordinate gelesen.
2. Ortsnamen werden über die geo.admin Search API in Schweizer LV95-Koordinaten
   umgewandelt.
3. Aus Mittelpunkt, Breite und Höhe wird eine LV95-Bounding-Box berechnet.
4. Die Bounding Box wird für die STAC-Suche nach WGS84 umgerechnet.
5. Über die swisstopo STAC API werden passende swissSURFACE3D-Rasterkacheln
   gesucht.
6. Die GeoTIFF-Kacheln werden heruntergeladen und gelesen.
7. Die Kacheln werden in ein gemeinsames Zielraster eingefügt.
8. Fehlende Höhenwerte werden mit dem tiefsten vorhandenen Wert aufgefüllt.
9. Koordinaten und Höhen werden auf die Modellgrösse skaliert.
10. Das Programm schreibt eine binäre STL-Datei.
11. Die STL enthält eine topografische Oberfläche, Seitenwände und eine
    flache Bodenplatte.

Die verwendete STAC-Collection ist:

```text
ch.swisstopo.swisssurface3d-raster
```

Datenquellen:

| Dienst | Zweck |
| --- | --- |
| geo.admin Search API | Ortsname zu LV95-Koordinate |
| swisstopo STAC API | Suche nach swissSURFACE3D-Kacheln |
| swissSURFACE3D Raster | Höhendaten für das Modell |

## Genauigkeit, Laufzeit und Dateigrösse

STL-Dateien können sehr gross werden. Die Dateigrösse hängt vor allem von
der Fläche, der Rasterauflösung und der Topografie ab. Kleine Auflösungswerte
wie `0.5 m/Pixel` liefern mehr Detail, erzeugen aber deutlich mehr Rasterpunkte
und Dreiecke.

Empfehlungen:

- Für maximale Kompatibilität `programm.py` verwenden.
- Für maximale Detailqualität Ausschnitte unter `1 km` pro Seite wählen.
- Nur so viel Umgebung wählen, wie wirklich gebraucht wird.
- Sehr grosse Gebiete können lange laufen und sehr grosse STL-Dateien erzeugen.
- Slicer und Viewer reagieren auf sehr grosse STL-Dateien unterschiedlich.

Der geführte Standardmodus reduziert das Risiko grosser, schwer ladbarer
Modelle, indem er:

- die Modellgrösse fest auf `100 mm` lange Seite setzt,
- den Sockel auf `3 mm` setzt,
- die STL in `mm` ausgibt,
- die Auflösung für grosse Ausschnitte automatisch grober wählt,
- planare Flächen konservativ zusammenfasst,
- steile Wandbereiche separat behandelt.

## API-Limits und Fair Use

Geo3Dprint verwendet geo.admin.ch und data.geo.admin.ch bewusst sparsam:

- eine Ortssuche über `api3.geo.admin.ch`, wenn ein Ortsname eingegeben wird,
- eine STAC-Suche über `data.geo.admin.ch` pro Modell,
- danach die gefundenen GeoTIFF-Kacheln als direkte Downloads.

Für allgemeine REST-Services von `*.geo.admin.ch` gelten seit 2025 als Fair Use
`40 requests / minute` und `21 Mio requests / year`. Geo3Dprint taktet HTTP-
Requests deshalb zentral auf maximal 40 Requests pro Minute, setzt einen
eigenen `User-Agent`, wiederholt temporäre Fehler und wartet bei `429 Too Many
Requests` gemäss `Retry-After` oder mit exponentiellem Backoff.

Heruntergeladene GeoTIFF-Tiles werden lokal unter `.tile_cache/` wiederverwendet.
Der Ordner ist in `.gitignore` eingetragen und wird nicht nach GitHub gepusht.
Mit `GEO3DPRINT_TILE_CACHE_DIR` kann ein anderer lokaler Cache-Pfad gesetzt werden.

## Koordinaten und Grenzen

LV95-Koordinaten müssen als `E N` eingegeben werden, zum Beispiel:

```text
2600000 1200000
```

WGS84-Koordinaten müssen als `lat lon` eingegeben werden, zum Beispiel:

```text
46.948 7.447
```

Das Programm prüft:

- ob Koordinaten plausibel in der Schweiz liegen,
- ob LV95- und WGS84-Koordinaten nicht vertauscht wurden,
- ob Breite und Höhe positiv sind,
- im geführten Standardmodus, ob der Ausschnitt maximal `5000 m x 5000 m`
  gross ist.

Der erweiterte Modus prüft weiterhin positive Breite und Höhe sowie plausible
Schweizer LV95-Koordinaten, erzwingt aber kein fixes Seitenlimit.

## CLI-Optionen für den erweiterten Modus

Diese Optionen gehören zu `programm_advanced.py`.

| Option | Beschreibung |
| --- | --- |
| `--out` | Pfad der STL-Ausgabedatei |
| `--res` | Rasterauflösung in Metern pro Pixel |
| `--zex` | Vertikale Höhenverstärkung |
| `--base` | Sockelhöhe, also Extrusion nach unten |
| `--bbox` | Bounding Box in LV95: `MIN_E MIN_N MAX_E MAX_N` |
| `--collection` | STAC-Collection-ID |
| `--interp` | Resampling: `nn` oder `bilinear` |
| `--no-interactive` | Keine Eingabeaufforderungen, nur CLI-Parameter verwenden |
| `--scale` | Modellmassstab als Nenner, z. B. `10000` für 1:10000 |
| `--unit` | Ausgabeeinheit für STL-Koordinaten: `m` oder `mm` |
| `--planar-merge` | Rechteck-Merge aktivieren |
| `--no-planar-merge` | Rechteck-Merge deaktivieren und Raster direkt schreiben |
| `--planar-tolerance` | Maximale Höhenabweichung für zusammengefasste Rechtecke |
| `--planar-max-seconds` | Optionales Zeitbudget für Rechteck-Merge; `0` bedeutet kein Zeitlimit |
| `--merge-workers` | Worker-Threads für Rechteck-Merge; `0` bedeutet automatisch |
| `--tile-workers` | Parallele Downloads und Dekodierungen für GeoTIFF-Kacheln |
| `--no-wall-merge` | Wand-Merge deaktivieren |
| `--wall-slope-threshold` | Minimale Steigung für erkannte Wandkanten |

## Projektstruktur

| Datei | Aufgabe |
| --- | --- |
| `programm.py` | Empfohlener geführter Standardmodus für maximale Kompatibilität |
| `programm_assistent.py` | Alternativer Startpunkt für denselben geführten Assistenten |
| `geo_assistent.py` | Geführter Ablauf, Kartenstart, vereinfachte Eingaben und automatischer Export |
| `programm_advanced.py` | Erweiterter interaktiver und nicht-interaktiver CLI-Modus |
| `geo_input.py` | Eingabevalidierung, Geocoding und Koordinatenumrechnung |
| `terrain_pipeline.py` | STAC-Suche, GeoTIFF-Lesen, Rasteraufbau, Optimierung und STL-Export |
| `requirements.txt` | Python-Abhängigkeiten |
| `Geo3Dprint-starten.bat` | Windows-Starter mit automatischer lokaler Python-Laufzeit |

## Fehlerfälle

Das Programm beendet sich mit einer Fehlermeldung, wenn:

- keine passenden swisstopo-Kacheln gefunden werden,
- keine Höhenpixel in den Ausschnitt fallen,
- Koordinaten ausserhalb der Schweiz liegen,
- der Ausschnitt ungültig ist,
- der Download einer benötigten Kachel fehlschlägt.
