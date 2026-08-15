#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Vorschaubilder in eine CLI-geslicete 3MF nachtragen
===================================================

Die Bambu-Studio-CLI rendert ohne OpenGL keine Vorschaubilder. Das ist fuer
den Druck selbst egal, aber `pick_1.png` ist das Bild, ueber das Bambus
Oberflaechen ein angeklicktes Objekt einer Objekt-ID zuordnen. Damit die
Testdatei strukturgleich zu einer aus der grafischen Oberflaeche ist und
fehlende Vorschauen als Fehlerquelle ausscheiden, werden sie hier nachgebaut.

Kodierung des Pick-Bildes (abgeleitet und geprueft an einer echten, von
Bambu Studio erzeugten Datei):

    id  = r + g*256 + b*65536
    px  = mm_x * 2          (512 px ueber 256 mm Bett)
    py  = 512 - mm_y * 2    (Bildursprung oben links, Bett vorne links)

Die Zuordnung wurde an zehn Objekten gegengerechnet; der Massstab lag bei
allen zwischen 1,994 und 2,005 px/mm.
"""

import io
import shutil
import zipfile

from PIL import Image, ImageDraw

import wuerfel_erzeugen as w

QUELLE = "Skiptest.gcode.3mf"
ZIEL = "Skiptest_final.gcode.3mf"
BETT = 256.0

# Objekt-IDs, wie der Slicer sie im G-Code vergeben hat. Bei einer Neuslicung
# mit anderer Objektzahl aendern sie sich — dann hier nachziehen (die Werte
# stehen im G-Code als "unique label id").
IDS = (83, 94, 105, 116)


def _mittelpunkt(mx, my, seite):
    f = seite / BETT
    return mx * f, seite - my * f


def _quadrat(zeichner, mx, my, farbe, seite):
    f = seite / BETT
    h = w.KANTE_XY / 2 * f
    cx, cy = _mittelpunkt(mx, my, seite)
    zeichner.rectangle([cx - h, cy - h, cx + h, cy + h], fill=farbe)


def bilder_bauen():
    pick = Image.new("RGB", (512, 512), (0, 0, 0))
    zp = ImageDraw.Draw(pick)
    for oid, mx in zip(IDS, w.X_POSITIONEN):
        _quadrat(zp, mx, w.Y,
                 (oid & 255, (oid >> 8) & 255, (oid >> 16) & 255), 512)
    pick.save("pick_1.png")

    for name, seite in (("plate_1.png", 512), ("plate_no_light_1.png", 512),
                        ("top_1.png", 512), ("plate_1_small.png", 128)):
        im = Image.new("RGBA", (seite, seite), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        for mx in w.X_POSITIONEN:
            _quadrat(d, mx, w.Y, (210, 210, 215, 255), seite)
        im.save(name)


def einfuegen():
    shutil.copy(QUELLE, ZIEL)
    alt = zipfile.ZipFile(QUELLE)
    neu = zipfile.ZipFile(ZIEL, "w", zipfile.ZIP_DEFLATED)
    for e in alt.infolist():
        neu.writestr(e, alt.read(e.filename))
    for f in ("pick_1.png", "plate_1.png", "plate_no_light_1.png",
              "top_1.png", "plate_1_small.png"):
        neu.writestr("Metadata/" + f, open(f, "rb").read())
    neu.close()
    alt.close()


def pruefen():
    """Pick-Bild zurueckdekodieren — die IDs muessen exakt herauskommen."""
    z = zipfile.ZipFile(ZIEL)
    im = Image.open(io.BytesIO(z.read("Metadata/pick_1.png"))).convert("RGB")
    gefunden = set()
    for r, g, b in im.get_flattened_data() if hasattr(im, "get_flattened_data") \
            else list(im.getdata()):
        i = r + g * 256 + b * 65536
        if i:
            gefunden.add(i)
    return sorted(gefunden)


if __name__ == "__main__":
    bilder_bauen()
    einfuegen()
    gefunden = pruefen()
    print("IDs im Pick-Bild:", gefunden)
    if gefunden != sorted(IDS):
        raise SystemExit("FEHLER: erwartet %s" % sorted(IDS))
    print("%s in Ordnung" % ZIEL)
