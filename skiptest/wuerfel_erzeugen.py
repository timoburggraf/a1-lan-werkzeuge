#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Vier Testwuerfel als STL. Nebeneinander, damit die Kamera sie einzeln
sieht und eine Luecke durch ein uebersprungenes Objekt sofort auffaellt."""
import struct

KANTE_XY = 14.0
HOEHE = 5.0
Y = 128.0
X_POSITIONEN = (100.0, 128.0, 156.0, 184.0)


def quader(pfad, bx, by, bz, mx, my):
    x0, x1 = mx - bx / 2, mx + bx / 2
    y0, y1 = my - by / 2, my + by / 2
    z0, z1 = 0.0, bz
    e = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
         (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    f = [(0, 3, 2), (0, 2, 1), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
         (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    with open(pfad, "wb") as g:
        g.write(b"\0" * 80)
        g.write(struct.pack("<I", len(f)))
        for a, b, c in f:
            ax, ay, az = e[a]
            u = (e[b][0] - ax, e[b][1] - ay, e[b][2] - az)
            v = (e[c][0] - ax, e[c][1] - ay, e[c][2] - az)
            n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2],
                 u[0] * v[1] - u[1] * v[0])
            L = (n[0] ** 2 + n[1] ** 2 + n[2] ** 2) ** 0.5 or 1.0
            g.write(struct.pack("<3f", *[k / L for k in n]))
            for p in (e[a], e[b], e[c]):
                g.write(struct.pack("<3f", *p))
            g.write(b"\0\0")


if __name__ == "__main__":
    for i, x in enumerate(X_POSITIONEN, start=1):
        quader("Wuerfel_%d.stl" % i, KANTE_XY, KANTE_XY, HOEHE, x, Y)
        print("Wuerfel_%d.stl  x=%.0f y=%.0f  %.0fx%.0fx%.0f mm"
              % (i, x, Y, KANTE_XY, KANTE_XY, HOEHE))
