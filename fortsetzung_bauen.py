#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fortsetzung bauen — abgebrochenen Druck ab der letzten Schicht weiterdrucken
===========================================================================

Nimmt den Schlussstand aus `journal/letzter.json` (oder eine ausdrueckliche
Schichtnummer) und die Originaldatei, und baut daraus einen Druckauftrag, der
das Startritual regulaer faehrt und dann bei der Abbruchschicht weitermacht.

Der springende Punkt ist die SICHERHEIT, nicht das Zusammenschneiden. Ein
Fortsetzungsdruck faehrt ueber bereits gedrucktes Material — deshalb prueft
dieses Skript vorher, ob das Startritual dem Werkstueck zu nahe kommt:

  * Wo tastet `G28 Z` an, und welche FLAECHE vermisst `G29 A1`?
  * Wo legt das Ritual Material ab (Purge-Linie)?
  * Liegt eines davon im Bereich der gedruckten Objekte?

Beim A1 liegen beide bei y ~254..261, also hinter dem Druckbereich — deshalb
darf das komplette Ritual mitlaufen und der Drucker homed regulaer. Waere das
nicht so, muesste man ohne Homing arbeiten und eine geratene Z-Referenz
benutzen; genau das lehnt dieses Skript ab (`--erzwingen` ueberstimmt es,
aber nur mit Ansage).

Nach dem Ritual faehrt die Ueberleitung ZUERST nach oben und erst dann
langsam auf die Fortsetzungshoehe — nach oben ist immer gefahrlos.

Aufruf:
    python3 fortsetzung_bauen.py original.gcode.3mf [--schicht N] [--ziel X.3mf]
    python3 fortsetzung_bauen.py original.gcode.3mf --wiederholen   # Abbruchschicht neu
"""

import argparse
import hashlib
import json
import os
import re
import sys
import zipfile

BASIS = os.path.dirname(os.path.abspath(__file__))
JOURNAL = os.path.join(BASIS, "journal", "letzter.json")


def gcode_aus(pfad):
    if pfad.endswith(".3mf"):
        return zipfile.ZipFile(pfad).read("Metadata/plate_1.gcode").decode(
            "utf-8", "replace")
    return open(pfad, encoding="utf-8", errors="replace").read()


BETT = (0.0, 256.0, 0.0, 256.0)      # A1: 256 x 256 mm


def _im_bett(x, y):
    return BETT[0] <= x <= BETT[1] and BETT[2] <= y <= BETT[3]


def ritual_punkte(kopf):
    """Alle Stellen (x,y), an denen das Ritual das Bett beruehrt.

    Beruecksichtigt Materialablage (E>0) und die Antastpunkte von G28 Z.
    """
    punkte = []
    x = y = 0.0
    for z in kopf.splitlines():
        t = z.strip()
        if t.startswith("G28 Z") and _im_bett(x, y):
            punkte.append((x, y))               # hier wird angetastet
        if not t.startswith(("G0", "G1")):
            continue
        mx = re.search(r"X([-\d.]+)", t)
        my = re.search(r"Y([-\d.]+)", t)
        me = re.search(r"\sE([\d.]+)", t)
        if mx: x = float(mx.group(1))
        if my: y = float(my.group(1))
        if me and float(me.group(1)) > 0 and _im_bett(x, y):
            punkte.append((x, y))
    return punkte


def ritual_flaechen(kopf):
    """Alle FLAECHEN, die das Ritual abtastet — bisher der blinde Fleck.

    `ritual_punkte()` sammelt Punkte; `G29 A1 X.. Y.. I.. J..` vermisst aber
    ein ganzes Rechteck, und Bambu bildet es aus der Objekt-Bounding-Box.
    Bei einer Fortsetzung liegt darunter also immer Werkstueck. Ein Punkttest
    kann ein Rechteck grundsaetzlich nicht finden — deshalb blieb der Konflikt
    unentdeckt, obwohl daneben "geprueft" im Code stand.
    """
    flaechen = []
    for m in re.finditer(r"G29 A1 X([\d.]+) Y([\d.]+) I([\d.]+) J([\d.]+)", kopf):
        x, y, i, j = (float(v) for v in m.groups())
        flaechen.append((x, x + i, y, y + j))
    return flaechen


def objekt_bereich(gcode):
    """(x0,x1,y0,y1) des tatsaechlich gedruckten Werkstuecks."""
    xs, ys = [], []
    x = y = 0.0
    koerper = gcode.split("; MACHINE_START_GCODE_END", 1)[-1]
    for z in koerper.splitlines():
        if not z.startswith(("G0", "G1")):
            continue
        mx = re.search(r"X([-\d.]+)", z)
        my = re.search(r"Y([-\d.]+)", z)
        me = re.search(r"\sE([\d.]+)", z)
        if mx: x = float(mx.group(1))
        if my: y = float(my.group(1))
        if me and float(me.group(1)) > 0 and _im_bett(x, y):
            xs.append(x); ys.append(y)
    return (min(xs), max(xs), min(ys), max(ys)) if xs else None


def bauen(original, schicht, ziel, erzwingen=False, rand=8.0):
    g = gcode_aus(original)
    if "; MACHINE_START_GCODE_END" not in g:
        raise SystemExit("Kein Startritual gefunden — ist das ein A1-G-Code?")
    kopf, rest = g.split("; MACHINE_START_GCODE_END", 1)
    kopf += "; MACHINE_START_GCODE_END"

    bloecke = rest.split("; CHANGE_LAYER")
    gesamt = len(bloecke) - 1
    if not 1 <= schicht <= gesamt:
        raise SystemExit("Schicht %d liegt nicht in 1..%d" % (schicht, gesamt))

    # --- Sicherheitspruefung -------------------------------------------
    rp = ritual_punkte(kopf)
    ob = objekt_bereich(g)
    print("Startritual beruehrt das Bett an %d Stellen" % len(rp))
    print("Werkstueck liegt bei  x %.1f..%.1f  y %.1f..%.1f" % ob)
    # Punktgenau: liegt eine Beruehrung IM Werkstueckbereich (plus Rand)?
    treffer = [(x, y) for x, y in rp
               if ob[0] - rand <= x <= ob[1] + rand
               and ob[2] - rand <= y <= ob[3] + rand]
    if treffer:
        print("  %d davon im Werkstueckbereich, z. B. %s"
              % (len(treffer), ["(%.0f,%.0f)" % t for t in treffer[:4]]))
    # Flaechen: G29 vermisst ein Rechteck, kein einzelner Punkt.
    flaechen = ritual_flaechen(kopf)
    ueberschneidung = [f for f in flaechen
                       if f[0] < ob[1] and ob[0] < f[1]
                       and f[2] < ob[3] and ob[2] < f[3]]
    if ueberschneidung:
        for f in ueberschneidung:
            print("  G29 vermisst x %.1f..%.1f  y %.1f..%.1f — ueberdeckt das "
                  "Werkstueck" % f)
        print("\n!!! Die Bettnivellierung taeste auf gedrucktem Material.")
        print("    Die G29-Zeile wird entfernt; beim Start muss zusaetzlich")
        print("    bed_leveling=False gesetzt werden (a1_befehle.druck_starten).")
        kopf = re.sub(r"^G29 A1 [^\n]*\n", "", kopf, flags=re.M)
        kopf = re.sub(r"^M500 ; save cali data\n", "", kopf, flags=re.M)

    if treffer:
        print("\n!!! Das Startritual kaeme dem Werkstueck naeher als %.0f mm."
              % rand)
        print("    Homing/Purge wuerden ueber gedrucktes Material fahren.")
        if not erzwingen:
            raise SystemExit("Abbruch. Mit --erzwingen ueberstimmen (auf eigene "
                             "Gefahr, dann bitte danebenstehen).")
        print("    --erzwingen gesetzt, mache trotzdem weiter.")
    else:
        print("-> sicher: Ritual und Werkstueck ueberschneiden sich nicht")

    weiter = "; CHANGE_LAYER" + "; CHANGE_LAYER".join(bloecke[schicht:])
    m = re.search(r"; Z_HEIGHT:\s*([\d.]+)", weiter)
    if not m:
        raise SystemExit("Keine Z_HEIGHT in der Fortsetzungsschicht gefunden")
    zh = float(m.group(1))

    # --- Schnittstelle saeubern ----------------------------------------
    # Gelernt am 15.08.2026: Ein Schnitt genau auf "; CHANGE_LAYER" landet
    # MITTEN in einer Sequenz. Der Block beginnt mit dem Abschluss der
    # VORIGEN Schicht: einem WIPE (lauter negative E = Rueckzuege), einem
    # "; stop printing object" und einem M625, dessen oeffnendes M624 im
    # abgeschnittenen Teil steht. Ergebnis beim ersten Versuch: der Drucker
    # zog rund 2 mm Filament zurueck, arbeitete eine unpaarige
    # Objektklammer ab — und foerderte nichts mehr.
    zeilen = weiter.splitlines()
    entfernt = []
    while zeilen:
        t = zeilen[0].strip()
        if (t.startswith("; WIPE_START") or t.startswith("; WIPE_END")
                or t.startswith("; stop printing object")
                or t == "M625"
                or (t.startswith(("G1", "G0")) and re.search(r"\sE-[\d.]+", t))
                or t.startswith(("G1 F", "M204 S"))
                or t.startswith("; Z_HEIGHT") or t.startswith("; LAYER_HEIGHT")
                or t == "; CHANGE_LAYER" or not t):
            entfernt.append(zeilen.pop(0))
            continue
        break
    weiter = "\n".join(zeilen)
    print("Schnittstelle gesaeubert: %d Zeilen Rest der Vorschicht entfernt "
          "(Rueckzuege, unpaariges M625)" % len(entfernt))

    # --- Warnung bei Farbwechsel in der Fortsetzungsschicht -------------
    kopf_der_schicht = weiter[:4000]
    if re.search(r"^M620 S\d+A", kopf_der_schicht, re.M):
        print("\n!!! Die Fortsetzungsschicht beginnt mit einem FARBWECHSEL.")
        print("    Der Wechsel-Ablauf setzt voraus, dass der Drucker weiss,")
        print("    welches Filament gerade geladen ist — diese Vorgeschichte")
        print("    steht im abgeschnittenen Teil. Bei Mehrfarbdrucken lieber")
        print("    eine Schicht OHNE Wechsel als Schnittstelle waehlen.")
        if not erzwingen:
            naechste = None
            for k in range(schicht + 1, min(schicht + 12, gesamt + 1)):
                if not re.search(r"^M620 S\d+A", bloecke[k][:4000], re.M):
                    naechste = k
                    break
            raise SystemExit(
                "Abbruch. Vorschlag: --schicht %s (erste Schicht ohne "
                "Farbwechsel) oder --erzwingen." % (naechste or "?"))

    bruecke = (
        "\n;===== FORTSETZUNG ab Schicht %d (Z %.3f mm) ==================\n"
        "; Erzeugt von fortsetzung_bauen.py. Das Startritual oben ist\n"
        "; unveraendert: der Drucker homet und nivelliert reguler, die\n"
        "; Antastpunkte geprueft (Punkte UND G29-Messflaeche); wo G29 das\n"
        "; Werkstueck ueberdeckte, wurde die Zeile entfernt -> Start mit\n"
        "; bed_leveling=False.\n"
        "G90\n"
        "G90\n"
        "M83              ; relative Extrusion, wie im Originalauftrag\n"
        "G1 Z%.3f F1200   ; erst HOCH — nach oben ist immer gefahrlos\n"
        "M400\n"
        "; Duese laden: der abgeschnittene Teil endete mit Rueckzuegen, die\n"
        "; hier fehlen. Ohne diesen Vorschub bliebe die Duese leer.\n"
        "G1 E3 F180\n"
        "G1 E-0.8 F1800   ; kurzer Rueckzug fuer die Fahrt\n"
        "M400\n"
        "G1 Z%.3f F600    ; dann langsam auf die Fortsetzungshoehe\n"
        "M400\n"
        ";===== ab hier originaler G-Code =============================\n"
        % (schicht, zh, max(zh + 10.0, 10.0), zh))

    neu = kopf + bruecke + weiter
    open(os.path.splitext(ziel)[0] + ".gcode", "w", encoding="utf-8").write(neu)

    if original.endswith(".3mf"):
        alt = zipfile.ZipFile(original)
        md5 = hashlib.md5(neu.encode("utf-8", "replace")).hexdigest()
        try:
            if alt.read("Metadata/plate_1.gcode.md5").decode().strip().isupper():
                md5 = md5.upper()
        except KeyError:
            pass
        aus = zipfile.ZipFile(ziel, "w", zipfile.ZIP_DEFLATED)
        for e in alt.infolist():
            d = alt.read(e.filename)
            if e.filename == "Metadata/plate_1.gcode":
                d = neu.encode("utf-8", "replace")
            elif e.filename == "Metadata/plate_1.gcode.md5":
                d = md5.encode()
            aus.writestr(e, d)
        aus.close()

    print("\n%s geschrieben: Schicht %d..%d von %d, Start bei Z %.3f mm"
          % (ziel, schicht, gesamt, gesamt, zh))
    return ziel


def main():
    p = argparse.ArgumentParser(description="Abgebrochenen Druck fortsetzen")
    p.add_argument("original", help="urspruengliche .gcode.3mf")
    p.add_argument("--schicht", type=int,
                   help="ab dieser Schicht (Vorgabe: aus dem Journal)")
    p.add_argument("--wiederholen", action="store_true",
                   help="die Abbruchschicht selbst nochmal drucken statt der "
                        "folgenden (fuer den Fall, dass sie halb fertig war)")
    p.add_argument("--ziel", help="Zieldatei (Vorgabe: fortsetzung.gcode.3mf)")
    p.add_argument("--erzwingen", action="store_true",
                   help="Sicherheitspruefung ueberstimmen")
    a = p.parse_args()

    schicht = a.schicht
    if schicht is None:
        if not os.path.exists(JOURNAL):
            raise SystemExit("Kein Journal unter %s — bitte --schicht angeben."
                             % JOURNAL)
        j = json.load(open(JOURNAL, encoding="utf-8"))
        abbruch = j.get("layer_num") or 0
        schicht = abbruch if a.wiederholen else abbruch + 1
        print("Journal: Auftrag %r, Abbruch in Schicht %s (%s)"
              % (j.get("gcode_file"), abbruch, j.get("zeit")))
        if j.get("s_obj"):
            print("  Hinweis: uebersprungene Objekte %s — die bleiben es auch."
                  % j["s_obj"])
        print("  -> Fortsetzung ab Schicht %d%s"
              % (schicht, " (Abbruchschicht wird wiederholt)"
                 if a.wiederholen else ""))
    ziel = a.ziel or os.path.join(BASIS, "fortsetzung.gcode.3mf")
    bauen(a.original, schicht, ziel, a.erzwingen)


if __name__ == "__main__":
    sys.exit(main())
