#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Baut die Prototyp-Platte als eine Bambu-taugliche Projekt-3MF.

Warum so: Der Slicer verweigert ueberlappende OBJEKTE ("partly inside"),
zweifarbige Teile brauchen deshalb die Studio-Struktur "ein Objekt mit
mehreren PARTS" — Traeger auf Extruder 1 (weiss), Schrift-Inlay auf
Extruder 2 (schwarz). Die Struktur ist von den funktionierenden
*_2farbig.3mf des ems14a-Projekts uebernommen; die STLs sind bereits auf
ihre Plattenpositionen verschoben, die Bauliste braucht daher nur
Identitaets-Transformationen.
"""

import struct
import zipfile

# (Objektname, [(STL-Name, Extruder), ...])
OBJEKTE = [
    # Beide Teile der Frontgruppe: Traeger auf Extruder 1 (weiss), Schrift-
    # Inlay auf Extruder 2 (schwarz). Beide mit Sichtflaeche nach OBEN.
    ("Gehaeuse_Front", [("Gehaeuse_Front", 1), ("Front_Schrift", 2)]),
    ("Front_Blende",   [("Front_Blende", 1), ("Blende_Schrift", 2)]),
]

ZIEL = "front_platte.3mf"


def stl_lesen(pfad):
    """Binaer-STL -> (Eckpunkte, Dreiecke) mit verschweissten Punkten."""
    punkte = []
    index = {}
    dreiecke = []
    with open(pfad, "rb") as f:
        f.seek(80)
        n = struct.unpack("<I", f.read(4))[0]
        for _ in range(n):
            d = f.read(50)
            ecken = []
            for v in range(3):
                p = struct.unpack_from("<3f", d, 12 + v * 12)
                i = index.get(p)
                if i is None:
                    i = len(punkte)
                    index[p] = i
                    punkte.append(p)
                ecken.append(i)
            dreiecke.append(tuple(ecken))
    return punkte, dreiecke


def mesh_xml(oid, name, punkte, dreiecke):
    z = ['  <object id="%d" type="model" name="%s">' % (oid, name),
         "   <mesh>", "    <vertices>"]
    z += ['     <vertex x="%.5f" y="%.5f" z="%.5f"/>' % p for p in punkte]
    z += ["    </vertices>", "    <triangles>"]
    z += ['     <triangle v1="%d" v2="%d" v3="%d"/>' % t for t in dreiecke]
    z += ["    </triangles>", "   </mesh>", "  </object>"]
    return "\n".join(z)


def bauen():
    xml = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<model unit="millimeter" xml:lang="en-US"'
           ' xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"'
           # OHNE diesen Namensraum ist "BambuStudio:3mfVersion" ein
           # ungueltiges XML-Praefix — der Marker wird ignoriert, die
           # eingebettete Konfiguration nicht gelesen, und der Slicer faellt
           # auf Vorgaben zurueck (Bett 35 C, kein Startritual, keine Stuetzen).
           ' xmlns:BambuStudio="http://schemas.bambulab.com/package/2021">',
           ' <metadata name="Application">BambuStudio-02.07.01.57</metadata>',
           ' <metadata name="BambuStudio:3mfVersion">1</metadata>',
           ' <metadata name="Title">ems14a Frontgruppe — Gehaeuse_Front + Front_Blende, Sichtflaeche oben</metadata>',
           ' <metadata name="Designer">Timo Burggraf</metadata>',
           ' <resources>']
    einstellungen = ['<?xml version="1.0" encoding="UTF-8"?>', "<config>"]
    bau = []
    oid = 2
    for name, teile in OBJEKTE:
        teil_ids = []
        for stl, _ex in teile:
            punkte, dreiecke = stl_lesen(stl + ".stl")
            xml.append(mesh_xml(oid, stl, punkte, dreiecke))
            teil_ids.append(oid)
            print("  %-20s Objekt %-3d %6d Punkte %6d Dreiecke"
                  % (stl, oid, len(punkte), len(dreiecke)))
            oid += 1
        huelle = oid
        oid += 1
        xml.append('  <object id="%d" type="model" name="%s">' % (huelle, name))
        xml.append("   <components>")
        for t in teil_ids:
            xml.append('    <component objectid="%d"/>' % t)
        xml.append("   </components>")
        xml.append("  </object>")
        bau.append('  <item objectid="%d" printable="1"/>' % huelle)

        einstellungen.append('  <object id="%d">' % huelle)
        einstellungen.append('    <metadata key="name" value="%s"/>' % name)
        einstellungen.append('    <metadata key="extruder" value="1"/>')
        for (stl, ex), t in zip(teile, teil_ids):
            einstellungen.append('    <part id="%d" subtype="normal_part">' % t)
            einstellungen.append('      <metadata key="name" value="%s"/>' % stl)
            einstellungen.append('      <metadata key="extruder" value="%d"/>' % ex)
            einstellungen.append('      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>')
            einstellungen.append('    </part>')
        einstellungen.append('  </object>')

    xml.append(" </resources>")
    xml.append(" <build>")
    xml += bau
    xml.append(" </build>")
    xml.append("</model>")
    einstellungen.append("</config>")

    inhalt_typen = ('<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
        ' <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
        ' <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n'
        ' <Default Extension="config" ContentType="application/xml"/>\n'
        '</Types>')
    beziehungen = ('<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        ' <Relationship Target="/3D/3dmodel.model" Id="rel-1" '
        'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n'
        '</Relationships>')

    with zipfile.ZipFile(ZIEL, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", inhalt_typen)
        z.writestr("_rels/.rels", beziehungen)
        z.writestr("3D/3dmodel.model", "\n".join(xml))
        z.writestr("Metadata/model_settings.config", "\n".join(einstellungen))
    print("geschrieben:", ZIEL)


if __name__ == "__main__":
    bauen()
