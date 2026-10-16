"""Inspect a small NWCACHE2 extraction with the production intersection code.

Usage: python tests/probe_nwd_cache.py cache x y z
CPython binary adapter for isolated diagnostics; no Autodesk API simulation.
"""
import os
import struct
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..',
                             'Prueba.extension', 'lib'))
from nwdcoord import surface


def read_string(stream):
    size = shift = 0
    while True:
        byte = stream.read(1)[0]
        size |= (byte & 127) << shift
        if byte < 128:
            return stream.read(size).decode('utf-8')
        shift += 7


def read_mesh(path):
    with open(path, 'rb') as f:
        def unpack(fmt):
            return struct.unpack('<' + fmt, f.read(struct.calcsize('<' + fmt)))
        assert f.read(8) == b'NWCACHE2'
        read_string(f)
        unpack('d')
        source = read_string(f)
        count = unpack('i')[0]
        lo, hi = unpack('3d'), unpack('3d')
        vertices, triangles, items = [], [], []
        for _ in range(count):
            name, route, kind = read_string(f), read_string(f), read_string(f)
            nv = unpack('i')[0]
            vertices.extend(unpack(str(nv * 3) + 'd'))
            nt = unpack('i')[0]
            start = len(triangles) // 3
            triangles.extend(unpack(str(nt * 3) + 'i'))
            items.append(NS(nombre=name, ruta=route, start=start, end=start + nt))
        assert not f.read(1), 'Unexpected trailing bytes'
    mesh = NS(vertices=vertices, triangulos=triangles, items=items,
              num_triangulos=len(triangles) // 3, minimos=lo, maximos=hi,
              caja=lambda: (lo, hi), source=source)
    mesh.item_de = lambda i: next((a for a in items if a.start <= i < a.end), None)
    return mesh


if __name__ == '__main__':
    mesh = read_mesh(sys.argv[1])
    p = surface.SuperficieNWD(mesh).cargar()
    x, y, z = map(float, sys.argv[2:5])
    selected = p.altura(x, y, z, surface.MAS_BAJA_DEBAJO)
    print('items={} triangles={} ray={} selected={}'.format(
        len(mesh.items), mesh.num_triangulos, (x, y, z), selected))
    print('All intersections:', p.ultimas_cotas)
    for altitude, triangle in p._impactos:
        print('z={:.6f} item={}'.format(altitude, mesh.item_de(triangle).ruta))
    if selected is None:
        print('Components in extraction (not necessarily intersected):')
        for route in sorted(set(item.ruta for item in mesh.items)):
            print(route)
