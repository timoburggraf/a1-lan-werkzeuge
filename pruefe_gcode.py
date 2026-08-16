#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Abnahmepruefung fuer erzeugten G-Code — VOR jedem Hochladen
===========================================================

Entstanden aus einem Schaden: Am 15.08.2026 forderte ein selbst gebauter
Auftrag Filament-Index 2 an, obwohl nur 0 und 1 deklariert waren. Der Drucker
nahm das woertlich, zog graues PETG aus AMS-Fach 2 und schob anschliessend
weisses PLA nach, ohne zu entladen — Verstopfung in der Vierfachkopplung.

Ursache war eine handgebaute Konfiguration mit **unterschiedlich langen
filament_*-Vektoren**. Die Gefahr stand da schon in SLICEN.md; geprueft wurde
sie nicht. Diese Datei macht die Pruefung verbindlich und maschinell.

Aufruf:
    python3 pruefe_gcode.py fertig.gcode.3mf [--ams 0,1]

Rueckgabe 0 = in Ordnung, 1 = Befund. Bei Befund NICHT hochladen.

WICHTIG — massgebliche Quelle ist der CONFIG_BLOCK IM G-CODE, nicht
`Metadata/project_settings.config`. Eine unabhaengige Gegenpruefung hat am
15.08.2026 gezeigt, dass beide auseinanderlaufen koennen: Der Container
beschrieb einen einfarbigen PLA-Druck bei 200 C ohne Stuetzen, gefahren wurde
sechsfarbig mit PETG bei 255 C. Diese Datei hat anfangs den Container gelesen
— also eine Fiktion geprueft und den Auftrag durchgelassen.
"""

import argparse
import collections
import json
import re
import sys
import zipfile

# Sollwerte des Startrituals fuer den A1 mit 0.4-Duese, an zwei unabhaengigen
# Dateien gegengeprueft (siehe SLICEN.md).
RITUAL_SOLL = {"G29": 10, "M900": 6, "M620": 19, "M621": 4, "M623": 18,
               "extrude_cali": 8, "M975": 10, "G1 E": 68}


def config_block(g):
    """Den wirksamen CONFIG_BLOCK aus dem G-Code lesen.

    Bambu schreibt ihn als `; schluessel = wert` — Strings mit `;` getrennt,
    Zahlen mit `,`. Das ist die einzige Quelle, die der Drucker wirklich
    ausfuehrt; alles im Container ist Beiwerk.
    """
    k = {}
    for zeile in g.split("\n"):
        m = re.match(r"^; ([a-z_0-9]+) = (.*)$", zeile)
        if not m:
            continue
        s, w = m.group(1), m.group(2)
        if ";" in w:
            werte = [x.strip().strip('"') for x in w.split(";")]
        elif "," in w:
            werte = [x.strip().strip('"') for x in w.split(",")]
        else:
            werte = [w.strip().strip('"')]
        k[s] = werte
    return k


def lade(pfad):
    if pfad.endswith(".3mf"):
        z = zipfile.ZipFile(pfad)
        g = z.read("Metadata/plate_1.gcode").decode("utf-8", "replace")
        try:
            behaelter = json.loads(
                z.read("Metadata/project_settings.config").decode())
        except KeyError:
            behaelter = {}
        return g, config_block(g), behaelter
    g = open(pfad, encoding="utf-8", errors="replace").read()
    return g, config_block(g), {}


def g29_rechteck(g):
    """Das Nivellier-Messrechteck aus `G29 A1 X.. Y.. I.. J..` lesen.

    Bambu bildet es aus der Objekt-Bounding-Box. Bei einem Fortsetzungsdruck
    liegt darunter also GARANTIERT Werkstueck — die Duese tastet dann auf
    Kunststoff statt auf Blech und `M500` schreibt das falsche Mesh fest.
    Diese Zeile wurde frueher nicht ausgewertet (nur `G28 Z` und Extrusionen).
    """
    m = re.search(r"G29 A1 X([\d.]+) Y([\d.]+) I([\d.]+) J([\d.]+)", g)
    if not m:
        return None
    x, y, i, j = (float(v) for v in m.groups())
    return (x, y, x + i, y + j)


def werkstueck_bbox(pfad):
    """Belegte Grundflaeche laut plate_1.json (alle Objekte + Wischturm)."""
    if not pfad.endswith(".3mf"):
        return None
    try:
        pj = json.loads(zipfile.ZipFile(pfad).read("Metadata/plate_1.json"))
    except Exception:
        return None
    # Der Wischturm ist kein Werkstueck: Wer ihn mitrechnet, misst den Brim
    # gegen eine Kante, die 40 mm neben dem Teil liegt, und bekommt negative
    # Ueberstaende.
    kaesten = [o["bbox"] for o in pj.get("bbox_objects", [])
               if o.get("bbox") and o.get("name") != "wipe_tower"]
    if not kaesten:
        return None
    return (min(k[0] for k in kaesten), min(k[1] for k in kaesten),
            max(k[2] for k in kaesten), max(k[3] for k in kaesten))


def _schicht_bereich(g, nummer, naehe=None):
    """Extrusionsbereich einer Schicht (x0,y0,x1,y1) oder None."""
    xs, ys, x, y, lage = [], [], 0.0, 0.0, 0
    for zeile in g.split("\n"):
        if zeile.startswith("; CHANGE_LAYER"):
            lage += 1
            if lage > nummer:
                break
        if lage != nummer or not zeile.startswith(("G0 ", "G1 ")):
            continue
        mx = re.search(r"X([-\d.]+)", zeile)
        my = re.search(r"Y([-\d.]+)", zeile)
        me = re.search(r" E([\d.]+)", zeile)
        if mx:
            x = float(mx.group(1))
        if my:
            y = float(my.group(1))
        if not (me and float(me.group(1)) > 0):
            continue
        if naehe and not (naehe[0] - 30 < x < naehe[2] + 30
                          and naehe[1] - 30 < y < naehe[3] + 30):
            continue
        xs.append(x)
        ys.append(y)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def brim_breite(g):
    """Wie weit reicht die erste Schicht ueber die Objektkontur hinaus?

    Das FEATURE-Label allein taugt nicht: Am 15.08. war `; FEATURE: Brim`
    vorhanden, die Breite aber 0 — das Teil loeste sich. Am 16.08. war es
    umgekehrt: kein Label, aber 4.6 mm Brim lagen wirklich auf dem Bett.

    Gemessen wird gegen die dritte Schicht statt gegen `plate_1.json`: deren
    bbox ist oft ein Schablonenrest und liefert dann negative Ueberstaende
    fuer Teile, die nachweislich gehalten haben.
    """
    kern = _schicht_bereich(g, 3)
    if not kern:
        return None
    erste = _schicht_bereich(g, 1, naehe=kern)
    if not erste:
        return None
    return max(kern[0] - erste[0], kern[1] - erste[1],
               erste[2] - kern[2], erste[3] - kern[3])


def werkstueck_objekte(pfad):
    """Zahl echter Objekte auf der Platte (ohne Wischturm), oder None."""
    if not pfad.endswith(".3mf"):
        return None
    try:
        pj = json.loads(zipfile.ZipFile(pfad).read("Metadata/plate_1.json"))
    except Exception:
        return None
    return len([o for o in pj.get("bbox_objects", [])
                if o.get("name") != "wipe_tower"])


def ueberlappt(a, b):
    return a and b and a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def pruefen(pfad, ams=None, material=None):
    """Abnahme. `ams` = freigegebene Faecher, `material` = freigegebene Typen.

    Massgeblich ist der CONFIG_BLOCK aus dem G-Code (`k`), nicht der Container
    (`behaelter`) — die beiden koennen voellig auseinanderlaufen.
    """
    g, k, behaelter = lade(pfad)
    befunde = []
    hinweise = []

    def w(schluessel, i=0, vorgabe=None):
        v = k.get(schluessel)
        return v[i] if v and i < len(v) else vorgabe

    if "; MACHINE_START_GCODE_END" not in g:
        befunde.append("Kein Startritual (MACHINE_START_GCODE_END fehlt) — "
                       "der Drucker wuerde ohne Nivellierung und ohne "
                       "AMS-Ladesequenz losfahren.")
        kopf, koerper = "", g
    else:
        kopf, koerper = g.split("; MACHINE_START_GCODE_END", 1)

    typen = k.get("filament_type", [])
    anzahl = len(typen)

    # --- 1. Welche Filamente zieht der Drucker wirklich? ------------------
    #     Drei unabhaengige Quellen, damit keine einzelne luegen kann.
    gefordert = set(int(x) for x in re.findall(r"M620 S(\d+)A", g))
    gefordert |= set(int(x) for x in re.findall(r"^T(\d+)\s*$", g, re.M))
    benutzt = w("filament", vorgabe=None)
    if k.get("filament"):                    # "; filament: 1,2,3" ist 1-basiert
        gefordert |= {int(x) - 1 for x in k["filament"] if x.strip().isdigit()}
    for platzhalter in (255, 1000):          # Bambu-Platzhalter, keine Faecher
        gefordert.discard(platzhalter)

    if anzahl:
        zuviel = {i for i in gefordert if i >= anzahl}
        if zuviel:
            befunde.append(
                "G-Code fordert Filament-Index %s an, deklariert sind nur %d "
                "(0..%d)." % (sorted(zuviel), anzahl, anzahl - 1))

    # DIE tragende Regel: jeder geforderte Index muss auf ein freigegebenes
    # Fach mit freigegebenem Material zeigen. Reine Existenz genuegt nicht —
    # bei sechs deklarierten Filamenten "existiert" auch das PETG in Fach 2.
    if ams is not None:
        fremd = sorted(i for i in gefordert if i not in set(ams))
        if fremd:
            befunde.append(
                "G-Code zieht aus Fach %s, freigegeben sind aber nur %s. Der "
                "Drucker nimmt die Nummer woertlich und laedt das PHYSISCHE "
                "Fach — Material dort: %s."
                % (fremd, sorted(ams),
                   ", ".join("%d=%s" % (i, typen[i] if i < anzahl else "?")
                             for i in fremd)))
    else:
        hinweise.append("Keine Fachfreigabe uebergeben — Herkunft ungeprueft.")

    if material is not None and anzahl:
        erlaubt = {m.upper() for m in material}
        falsch = sorted((i, typen[i]) for i in gefordert
                        if i < anzahl and typen[i].upper() not in erlaubt)
        if falsch:
            befunde.append(
                "G-Code zieht Material %s, freigegeben ist nur %s."
                % (", ".join("Fach %d=%s" % f for f in falsch),
                   "/".join(sorted(erlaubt))))

    # --- 2. Stuetzfilament — die Falle, die den PETG-Zug ausgeloest hat ---
    #     Bambu zaehlt hier 1-basiert; 0 heisst "wie das Objekt".
    stuetzen_an = w("enable_support", vorgabe="0") not in ("0", "", None)
    for schluessel in ("support_interface_filament", "support_filament"):
        roh = w(schluessel, vorgabe="0")
        try:
            nr = int(roh)
        except (TypeError, ValueError):
            continue
        if nr == 0:
            continue
        idx = nr - 1
        art = typen[idx] if idx < anzahl else "?"
        if stuetzen_an and (ams is not None and idx not in set(ams)):
            befunde.append(
                "%s = %d zieht Fach %d (%s) — nicht freigegeben. Genau so kam "
                "am 15.08. PETG in den Extruder: Der Prozess setzte "
                "enable_support, liess aber %s auf dem CLI-Vorgabewert 3."
                % (schluessel, nr, idx, art, schluessel))
        elif not stuetzen_an:
            hinweise.append(
                "%s = %d steht scharf, zuendet nur nicht (enable_support = 0). "
                "Sobald Stuetzen eingeschaltet werden, zieht der Auftrag "
                "Fach %d (%s)." % (schluessel, nr, idx, art))

    # --- Stuetzen: Luftspalt oder Fremdmaterial, eines von beidem ---------
    # Am 16.08.2026 waren die Stuetzen nicht mehr vom Werkstueck zu loesen.
    # Ursache: Die Parameter stammten aus dem PETG-Trennschicht-Rezept (dort
    # ist z=0 richtig, weil PLA und PETG kaum haften), aber das PETG war
    # entfernt worden. Uebrig blieb: kein Spalt, geschlossene Interface-
    # Flaeche, verzahntes Muster — und dasselbe Material auf beiden Seiten.
    if stuetzen_an:
        z_ab = w("support_top_z_distance", vorgabe="0")
        try:
            z_ab = float(z_ab)
        except (TypeError, ValueError):
            z_ab = 0.0
        # Fremdmaterial? Nur dann darf der Spalt entfallen.
        nr_if = w("support_interface_filament", vorgabe="0")
        try:
            idx_if = int(nr_if) - 1
        except (TypeError, ValueError):
            idx_if = -1
        objekt_typ = typen[0].upper() if typen else ""
        fremd = (0 <= idx_if < anzahl
                 and typen[idx_if].upper() != objekt_typ)
        if z_ab < 0.05 and not fremd:
            befunde.append(
                "Stuetzen mit support_top_z_distance = %.2f, aber ohne "
                "Trennmaterial (Interface-Filament %s). Ohne Spalt UND ohne "
                "Fremdmaterial verschweisst die Stuetze mit dem Werkstueck "
                "und ist nicht mehr loesbar. Entweder z >= 0.2 setzen oder "
                "ein Fremdmaterial als Interface." % (z_ab, nr_if))
        muster = w("support_interface_pattern", vorgabe="")
        abstand = w("support_interface_spacing", vorgabe="0")
        if not fremd:
            if muster == "rectilinear_interlaced":
                hinweise.append(
                    "support_interface_pattern = rectilinear_interlaced "
                    "verzahnt sich mit dem Werkstueck und hinterlaesst die "
                    "schlimmsten Narben. Ohne Fremdmaterial ist `concentric` "
                    "die leichter loesbare Wahl.")
            try:
                if float(abstand) < 0.2:
                    hinweise.append(
                        "support_interface_spacing = %s ergibt eine "
                        "geschlossene Kontaktflaeche. Ohne Fremdmaterial "
                        "0.4-0.6 mm setzen, sonst fehlt die Sollbruchstelle."
                        % abstand)
            except (TypeError, ValueError):
                pass
        if w("support_style", vorgabe="") == "tree_slim":
            hinweise.append(
                "support_style = tree_slim erzeugt viele duenne Aeste — an "
                "feinen Rueckseitenstrukturen sind die spaeter nicht mehr zu "
                "greifen. Fuer flache Funktionsteile `snug` mit "
                "support_type `normal(auto)`.")

    if ams is not None and anzahl and len(ams) < len(gefordert):
        befunde.append(
            "ams_mapping hat %d Eintraege, der G-Code fordert %d Filamente."
            % (len(ams), len(gefordert)))

    # --- 3. Nivellierung ueber vorhandenem Werkstueck? -------------------
    lagen = re.search(r"layer num/total_layer_count: *(\d+)/(\d+)", g)
    startlage = int(lagen.group(1)) if lagen else 1
    if startlage > 1:
        mess = g29_rechteck(g)
        stueck = werkstueck_bbox(pfad)
        if mess and stueck and ueberlappt(mess, stueck):
            befunde.append(
                "Fortsetzung ab Schicht %d, aber das Startritual nivelliert "
                "mit `G29 A1` ueber X %.0f..%.0f / Y %.0f..%.0f — dort liegt "
                "Werkstueck (X %.0f..%.0f / Y %.0f..%.0f). Die Duese tastet "
                "auf Kunststoff, `M500` speichert das falsche Mesh. Entweder "
                "die G29-Zeile entfernen oder mit bed_leveling=False starten."
                % (startlage, mess[0], mess[2], mess[1], mess[3],
                   stueck[0], stueck[2], stueck[1], stueck[3]))
        elif mess and stueck is None:
            hinweise.append("Fortsetzungsdruck mit G29, aber keine "
                            "Werkstueckflaeche ermittelbar — von Hand pruefen.")

    # --- 4. Startritual vollstaendig? -----------------------------------
    for marke, soll in RITUAL_SOLL.items():
        ist = kopf.count(marke)
        if ist == 0:
            befunde.append("Startritual: %s fehlt ganz (erwartet %dx)."
                           % (marke, soll))
        elif ist < soll * 0.5:
            hinweise.append("Startritual: %s nur %dx statt %dx" % (marke, ist, soll))

    # --- 5. Temperaturen -------------------------------------------------
    bett = re.findall(r"^M190 S(\d+)", g, re.M)
    duese = sorted((int(x) for x in re.findall(r"^M109 S(\d+)", g, re.M)),
                   reverse=True)
    if not bett:
        befunde.append("Keine Betttemperatur (M190) gesetzt.")
    elif int(bett[0]) < 50:
        befunde.append("Betttemperatur nur %s C — bei PLA/PETG haftet nichts. "
                       "Meist steht curr_bed_type falsch auf 'Cool Plate'."
                       % bett[0])
    if not duese:
        befunde.append("Keine Duesentemperatur (M109) gesetzt.")
    elif duese[0] < 150:
        befunde.append("Hoechste Duesentemperatur nur %d C — es wuerde nichts "
                       "schmelzen." % duese[0])

    # --- 6. Weicht der Container vom wirklich gefahrenen Code ab? --------
    #     Kein Sperrgrund, aber jede Abnahme, die den Container liest, prueft
    #     dann eine Fiktion — deshalb sichtbar machen.
    abweichung = []
    for schluessel in ("filament_type", "filament_colour", "nozzle_temperature",
                       "enable_support", "support_interface_filament"):
        c = behaelter.get(schluessel)
        if c is None:
            continue
        c = [str(x) for x in (c if isinstance(c, list) else [c])]
        if c != [str(x) for x in k.get(schluessel, [])]:
            abweichung.append(schluessel)
    if abweichung:
        hinweise.append(
            "project_settings.config beschreibt einen ANDEREN Druck als der "
            "G-Code (%s). Massgeblich ist der G-Code." % ", ".join(abweichung))

    # --- 7. Geometrie ----------------------------------------------------
    objekte = set(re.findall(r"unique label id:\s*(\d+)", g))
    f = collections.Counter(re.findall(r"; FEATURE: ([^\n]+)", g))
    breite = brim_breite(g)
    if breite is not None and breite < 0.5 and f.get("Brim", 0) == 0:
        hinweise.append(
            "Keine Haftungshilfe: die erste Schicht reicht nur %.1f mm ueber "
            "die Objektkante. Bei kleiner Aufstandsflaeche loest sich das "
            "Teil." % breite)
    # Klammern nur einfordern, wo Ueberspringen ueberhaupt Sinn ergibt:
    # bei einem einzigen Objekt gaebe es nichts zu skippen ausser dem
    # ganzen Druck, und Bambu setzt dort bewusst keine Klammern.
    if not re.search(r"^M624", g, re.M) and len(objekte) != 1:
        n_obj = werkstueck_objekte(pfad)
        if n_obj is None or n_obj > 1:
            hinweise.append(
                "Keine M624/M625-Objektklammern bei %s Objekten — "
                "`skip_objects` hat an dieser Datei keine Angriffsflaeche."
                % (n_obj if n_obj is not None else "mehreren"))

    return {
        "befunde": befunde,
        "hinweise": hinweise,
        "filamente_deklariert": anzahl,
        "filamente_typen": typen,
        "filamente_gefordert": sorted(gefordert),
        "startlage": startlage,
        "bett": bett[:1], "duese": duese[:1],
        "objekte": len(objekte),
        "brim": f.get("Brim", 0),
        "brim_mm": breite,
        "stuetzen": sum(n for x, n in f.items() if "upport" in x),
        "wechsel": koerper.count("M620 S0A") + koerper.count("M620 S1A"),
    }


def main():
    p = argparse.ArgumentParser(description="G-Code vor dem Hochladen pruefen")
    p.add_argument("datei")
    p.add_argument("--ams", help="freigegebene AMS-Faecher, z. B. 0,1")
    p.add_argument("--material", help="freigegebene Materialtypen, z. B. PLA")
    a = p.parse_args()
    ams = [int(x) for x in a.ams.split(",")] if a.ams else None
    mat = [x.strip() for x in a.material.split(",")] if a.material else None

    r = pruefen(a.datei, ams, mat)
    print("Filamente deklariert : %d  (%s)"
          % (r["filamente_deklariert"], ";".join(r["filamente_typen"][:6])))
    print("Filamente gefordert  : %s%s"
          % (r["filamente_gefordert"],
             "".join("  [%d=%s]" % (i, r["filamente_typen"][i])
                     for i in r["filamente_gefordert"]
                     if i < r["filamente_deklariert"])))
    print("Startlage            : %d" % r["startlage"])
    print("Bett / Duese         : %s / %s" % (r["bett"], r["duese"]))
    print("Objekte / Brim / Stuetzen / Wechsel : %d / %d / %d / %d"
          % (r["objekte"], r["brim"], r["stuetzen"], r["wechsel"]))
    for h in r["hinweise"]:
        print("  Hinweis: %s" % h)
    if r["befunde"]:
        print("\n!!! NICHT HOCHLADEN — %d Befund(e):" % len(r["befunde"]))
        for b in r["befunde"]:
            print("  * %s" % b)
        return 1
    print("\n-> Abnahme bestanden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
