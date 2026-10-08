"""Codificador mínimo de Mapbox Vector Tile, SOLO para pruebas (genera tiles falsos de tráfico)."""
import struct


def varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def zz(n):
    return (n << 1) ^ (n >> 63)


def field(num, wire, payload):
    key = varint((num << 3) | wire)
    if wire == 0:
        return key + varint(payload)
    if wire == 5:
        return key + payload
    return key + varint(len(payload)) + payload


def value_msg(v):
    if isinstance(v, str):
        return field(1, 2, v.encode())
    if isinstance(v, float):
        return field(2, 5, struct.pack("<f", v))
    if isinstance(v, bool):
        return field(7, 0, int(v))
    return field(6, 0, zz(v))


def line_geom(pts):
    g, (px, py) = [(1 << 3) | 1], pts[0]
    g += [zz(px), zz(py)]
    g.append(((len(pts) - 1) << 3) | 2)
    for x, y in pts[1:]:
        g += [zz(x - px), zz(y - py)]
        px, py = x, y
    return g


def encode_tile(features, layer="Traffic flow", extent=4096):
    """features: [(props_dict, [(x,y),...]), ...] → bytes MVT."""
    keys, values, feats = [], [], b""
    for props, pts in features:
        tags = []
        for k, v in props.items():
            if k not in keys:
                keys.append(k)
            vm = value_msg(v)
            if vm not in values:
                values.append(vm)
            tags += [keys.index(k), values.index(vm)]
        packed = lambda xs: b"".join(varint(x) for x in xs)
        feats += field(2, 2, field(2, 2, packed(tags)) + field(3, 0, 2) + field(4, 2, packed(line_geom(pts))))
    body = field(1, 2, layer.encode()) + feats
    body += b"".join(field(3, 2, k.encode()) for k in keys) + b"".join(field(4, 2, v) for v in values) + field(5, 0, extent)
    return field(3, 2, body)
