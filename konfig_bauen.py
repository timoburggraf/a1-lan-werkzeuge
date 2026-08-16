#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Druckkonfiguration sauber auf N Filamente zuschneiden
=====================================================

Die Referenzkonfiguration stammt aus einem GUI-Projekt mit sechs
AMS-Faechern. Wer daraus einen Zweifarbdruck baut, muss **jeden**
`filament_*`-Vektor auf zwei Eintraege bringen — nicht nur die, die einem
gerade einfallen.

Warum das keine Kosmetik ist (15.08.2026, mit Sachschaden gelernt): Bleiben
die Vektoren unterschiedlich lang, gibt der Slicer Werkzeugwechsel auf
Filamente aus, die es nicht gibt. Der Drucker nimmt die Nummer woertlich und
zieht aus dem gleichnamigen PHYSISCHEN AMS-Fach — bei uns graues PETG statt
weissem PLA, mit Verstopfung der Vierfachkopplung als Folge.

Regeln beim Zuschneiden:
  * Liste laenger als N  -> auf die ersten N kuerzen
  * Liste kuerzer als N  -> mit dem letzten Wert auffuellen
  * NxN-Matrix (Spuelvolumen) -> auf die NxN-Untermatrix
"""

import json

# Listen, die zufaellig so lang sind wie die Filamentzahl, aber NICHTS mit
# Filamenten zu tun haben. Sie duerfen nicht zugeschnitten werden.
KEINE_FILAMENTLISTEN = {
    "printable_area", "bed_exclude_area", "head_wrap_detect_zone",
    "upward_compatible_machine", "different_settings_to_system",
    "extruder_offset", "thumbnails", "thumbnail_size",
    "machine_max_acceleration_e", "machine_max_acceleration_extruding",
    "machine_max_acceleration_retracting", "machine_max_acceleration_travel",
    "machine_max_acceleration_x", "machine_max_acceleration_y",
    "machine_max_acceleration_z", "machine_max_jerk_e", "machine_max_jerk_x",
    "machine_max_jerk_y", "machine_max_jerk_z", "machine_max_speed_e",
    "machine_max_speed_x", "machine_max_speed_y", "machine_max_speed_z",
    "machine_min_extruding_rate", "machine_min_travel_rate",
}


def zuschneiden(konfig, n, farben=None):
    """Alle filament_*-Vektoren auf genau n Eintraege bringen.

    Rueckgabe: (neue_konfig, bericht) — der Bericht listet, was angefasst
    wurde, damit man es gegenlesen kann statt es zu glauben.
    """
    k = dict(konfig)
    k.pop("name", None)
    k.pop("from", None)

    alt_n = len(k.get("filament_settings_id", []))
    bericht = {"vorher": alt_n, "nachher": n, "gekuerzt": [], "aufgefuellt": [],
               "matrix": [], "gruppiert": []}

    for s, v in list(k.items()):
        if not isinstance(v, list) or s in KEINE_FILAMENTLISTEN:
            continue
        # Filamentbezogen ist NICHT nur, was mit "filament_" anfaengt: auch
        # nozzle_temperature, textured_plate_temp, flush_volumes_matrix und
        # saemtliche Luefterparameter haben einen Wert je Filament. Am
        # 16.08.2026 hat der Slicer deshalb "Flush volumes matrix do not match
        # to the correct size" geworfen — 69 Vektoren waren uebersehen worden,
        # weil nur nach dem Namenspraefix gefiltert wurde.
        passend = (s.startswith("filament_") or len(v) == alt_n
                   or (alt_n and len(v) in (alt_n * alt_n, 2 * alt_n)))
        if not passend:
            continue
        # Quadratische Matrix? (Spuelvolumen ist alt_n x alt_n)
        if alt_n and len(v) == alt_n * alt_n:
            neu = [v[i * alt_n + j] for i in range(n) for j in range(n)]
            k[s] = neu
            bericht["matrix"].append(s)
            continue
        # Vielfaches: pro Filament stecken m Werte drin (z. B. Trocknungs-
        # profile mit 4 Werten je Fach). Stumpf auf n zu kuerzen wuerde die
        # Gruppen zerreissen — es muessen die ersten n GRUPPEN bleiben.
        if alt_n and len(v) > alt_n and len(v) % alt_n == 0:
            m = len(v) // alt_n
            k[s] = [v[i * m + j] for i in range(n) for j in range(m)]
            bericht["gruppiert"].append("%s (%d Werte je Filament)" % (s, m))
            continue
        if len(v) > n:
            k[s] = v[:n]
            bericht["gekuerzt"].append(s)
            if not s.startswith("filament_"):
                bericht.setdefault("ohne_praefix", []).append(s)
        elif len(v) < n:
            fuell = v[-1] if v else "0"
            k[s] = list(v) + [fuell] * (n - len(v))
            bericht["aufgefuellt"].append(s)

    if farben:
        k["filament_colour"] = list(farben)[:n]

    return k, bericht


def pruefen(konfig):
    """Sind jetzt alle filament_*-Vektoren gleich lang? Rueckgabe: (ok, laengen)."""
    laengen = {s: len(v) for s, v in konfig.items()
               if isinstance(v, list) and s not in KEINE_FILAMENTLISTEN
               and (s.startswith("filament_") or len(v) > 1)}
    n = len(konfig.get("filament_settings_id", []))
    # Matrizen duerfen n*n sein, alles andere muss n sein
    # Erlaubt: n (ein Wert je Filament), n*n (Matrix) oder n*m (Gruppen)
    abweichung = {s: l for s, l in laengen.items()
                  if l != n and l != n * n and (l % n != 0 or l // n > 8)}
    return (not abweichung), abweichung


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit("Aufruf: konfig_bauen.py referenz.json N [ziel.json]")
    quelle, n = sys.argv[1], int(sys.argv[2])
    ziel = sys.argv[3] if len(sys.argv) > 3 else None
    k, b = zuschneiden(json.load(open(quelle, encoding="utf-8")), n)
    ok, abw = pruefen(k)
    print("Filamente: %d -> %d" % (b["vorher"], b["nachher"]))
    print("  gekuerzt   : %d Vektoren" % len(b["gekuerzt"]))
    print("  aufgefuellt: %d Vektoren" % len(b["aufgefuellt"]))
    print("  Matrizen   : %s" % (b["matrix"] or "keine"))
    print("  Gruppen    : %s" % (b["gruppiert"] or "keine"))
    print("Einheitlich: %s" % ("ja" if ok else "NEIN — %s" % abw))
    if ziel:
        json.dump(k, open(ziel, "w", encoding="utf-8"), indent=1)
        print("geschrieben:", ziel)
