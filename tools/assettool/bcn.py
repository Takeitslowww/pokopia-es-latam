from __future__ import annotations

import numpy as np

CHUNK = 8192
RGB_FLOOR = 1e-4

BC7_WEIGHTS4 = np.array([0, 4, 9, 13, 17, 21, 26, 30, 34, 38, 43, 47, 51, 55, 60, 64], np.int64)
BC1_WEIGHTS4 = np.array([0.0, 1.0, 1.0 / 3.0, 2.0 / 3.0])
BC1_WEIGHTS3 = np.array([0.0, 1.0, 0.5, 0.0])


class BcnError(ValueError):
    pass


def image_blocks(rgba: np.ndarray) -> np.ndarray:
    rgba = np.asarray(rgba, np.uint8)
    h, w = rgba.shape[:2]
    ph, pw = -h % 4, -w % 4
    if ph or pw:
        rgba = np.pad(rgba, ((0, ph), (0, pw), (0, 0)), mode="edge")
    hb, wb = rgba.shape[0] // 4, rgba.shape[1] // 4
    b = rgba.reshape(hb, 4, wb, 4, 4).transpose(0, 2, 1, 3, 4)
    return np.ascontiguousarray(b.reshape(hb * wb, 16, 4))


def channel_weights(blocks: np.ndarray, channels: int = 4) -> np.ndarray:
    a = blocks[:, :, 3:4].astype(np.float64) / 255.0
    rgb = np.maximum(a * a, RGB_FLOOR)
    if channels == 3:
        return np.repeat(rgb, 3, axis=2)
    return np.concatenate([np.repeat(rgb, 3, axis=2), np.ones_like(a)], axis=2)


def _principal_axis(pix: np.ndarray, w: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    ws = w.sum(1, keepdims=True)
    mean = (pix * w[..., None]).sum(1) / ws
    d = pix - mean[:, None, :]
    cov = np.einsum("npi,npj,np->nij", d, d, w)
    diag = np.einsum("nii->ni", cov)
    v = np.zeros_like(mean)
    v[np.arange(len(v)), diag.argmax(1)] = 1.0
    for _ in range(8):
        v = np.einsum("nij,nj->ni", cov, v)
        norm = np.sqrt((v * v).sum(1, keepdims=True))
        v = np.where(norm > 1e-12, v / np.maximum(norm, 1e-12), 0.0)
    return mean, v


def _axis_endpoints(pix: np.ndarray, w: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mean, v = _principal_axis(pix, w)
    t = ((pix - mean[:, None, :]) * v[:, None, :]).sum(2)
    lo = mean + v * t.min(1, keepdims=True)
    hi = mean + v * t.max(1, keepdims=True)
    return np.clip(lo, 0, 255), np.clip(hi, 0, 255)


def _box_endpoints(pix: np.ndarray, visible: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]:
    c = pix.shape[2]
    vis = visible[..., None]
    any_vis = visible.any(1)[:, None]
    lo = np.where(vis, pix, 255.0).min(1)
    hi = np.where(vis, pix, 0.0).max(1)
    lo = np.where(any_vis, lo, pix.min(1))
    hi = np.where(any_vis, hi, pix.max(1))
    if c < 4:
        return [(lo, hi)]
    amin, amax = pix[:, :, 3].min(1), pix[:, :, 3].max(1)
    a = lo.copy(); a[:, 3] = amin
    b = hi.copy(); b[:, 3] = amax
    flip_lo = lo.copy(); flip_lo[:, 3] = amax
    flip_hi = hi.copy(); flip_hi[:, 3] = amin
    return [(a, b), (flip_lo, flip_hi)]


def _lsq_endpoints(pix: np.ndarray, W: np.ndarray, a: np.ndarray,
                   e0: np.ndarray, e1: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    b = (1.0 - a)[..., None]
    a = a[..., None]
    A = (W * b * b).sum(1)
    B = (W * a * b).sum(1)
    C = (W * a * a).sum(1)
    r0 = (W * b * pix).sum(1)
    r1 = (W * a * pix).sum(1)
    det = A * C - B * B
    ok = det > 1e-9
    safe = np.where(ok, det, 1.0)
    n0 = (C * r0 - B * r1) / safe
    n1 = (A * r1 - B * r0) / safe
    return np.clip(np.where(ok, n0, e0), 0, 255), np.clip(np.where(ok, n1, e1), 0, 255)


def _nearest(pix: np.ndarray, pal: np.ndarray, W: np.ndarray, forbid: np.ndarray | None = None):
    d = pix[:, :, None, :] - pal[:, None, :, :]
    e = (d * d * W[:, :, None, :]).sum(3)
    if forbid is not None:
        e = np.where(forbid[:, None, :], np.inf, e)
    idx = e.argmin(2)
    return idx, np.take_along_axis(e, idx[..., None], 2)[..., 0].sum(1)


def _keep(better: np.ndarray, new, old):
    m = better.reshape(better.shape + (1,) * (np.ndim(old) - 1))
    return np.where(m, new, old)


def _chunks(n: int):
    for s in range(0, n, CHUNK):
        yield s, min(n, s + CHUNK)


def _to565(c: np.ndarray) -> np.ndarray:
    c = np.clip(np.rint(c), 0, 255).astype(np.int64)
    r = (c[:, 0] * 31 + 127) // 255
    g = (c[:, 1] * 63 + 127) // 255
    b = (c[:, 2] * 31 + 127) // 255
    return (r << 11) | (g << 5) | b


def _from565(v: np.ndarray) -> np.ndarray:
    r, g, b = (v >> 11) & 31, (v >> 5) & 63, v & 31
    return np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)], 1)


def _bc1_palette(c0: np.ndarray, c1: np.ndarray, four: np.ndarray) -> np.ndarray:
    p0, p1 = _from565(c0), _from565(c1)
    p2 = np.where(four[:, None], (2 * p0 + p1) // 3, (p0 + p1) // 2)
    p3 = np.where(four[:, None], (p0 + 2 * p1) // 3, 0)
    return np.stack([p0, p1, p2, p3], 1)


def _color_block(blocks: np.ndarray, three: np.ndarray) -> np.ndarray:
    n = len(blocks)
    pix = blocks[:, :, :3].astype(np.float64)
    W = channel_weights(blocks, 3) * np.array([0.5, 1.0, 0.25])
    clear = three[:, None] & (blocks[:, :, 3] < 128)
    W = np.where(clear[..., None], 0.0, W)
    visible = (W[:, :, 1] > 0.1) & ~clear
    pw = np.where(W[:, :, 1].sum(1, keepdims=True) > 0, W[:, :, 1], 1.0)
    four = ~three
    forbid = np.zeros((n, 4), bool)
    forbid[:, 3] = three
    shares = np.where(four[:, None, None], BC1_WEIGHTS4, BC1_WEIGHTS3)[:, 0, :]
    best_err = np.full(n, np.inf)
    best_c0 = np.zeros(n, np.int64)
    best_c1 = np.zeros(n, np.int64)
    best_idx = np.zeros((n, 16), np.int64)
    cands = [_axis_endpoints(pix, pw)] + _box_endpoints(pix, visible)
    for it in range(3):
        nxt = []
        for e0, e1 in cands:
            c0, c1 = _to565(e0), _to565(e1)
            swap = np.where(four, c0 < c1, c0 > c1)
            c0, c1 = np.where(swap, c1, c0), np.where(swap, c0, c1)
            pal = _bc1_palette(c0, c1, four)
            idx, err = _nearest(pix, pal.astype(np.float64), W, forbid)
            better = err < best_err
            best_err = np.where(better, err, best_err)
            best_c0, best_c1 = _keep(better, c0, best_c0), _keep(better, c1, best_c1)
            best_idx = _keep(better, idx, best_idx)
            if it < 2:
                a = np.take_along_axis(shares, idx, 1)
                nxt.append(_lsq_endpoints(pix, W, a, _from565(c0).astype(np.float64),
                                          _from565(c1).astype(np.float64)))
        cands = nxt
    best_idx = np.where((best_c0 == best_c1)[:, None] & ~clear, 0, best_idx)
    best_idx = np.where(clear, 3, best_idx)
    bits = (best_idx << (2 * np.arange(16, dtype=np.int64))).sum(1)
    out = np.zeros((n, 8), np.uint8)
    out[:, 0], out[:, 1] = best_c0 & 255, best_c0 >> 8
    out[:, 2], out[:, 3] = best_c1 & 255, best_c1 >> 8
    for k in range(4):
        out[:, 4 + k] = (bits >> (8 * k)) & 255
    return out


def _alpha_palette(a0: np.ndarray, a1: np.ndarray) -> np.ndarray:
    eight = a0 > a1
    k = np.arange(8, dtype=np.int64)
    p8 = ((8 - k) * a0[:, None] + (k - 1) * a1[:, None]) // 7
    p6 = ((6 - k) * a0[:, None] + (k - 1) * a1[:, None]) // 5
    p6 = np.where(k == 6, 0, np.where(k == 7, 255, p6))
    pal = np.where(eight[:, None], p8, p6)
    pal[:, 0], pal[:, 1] = a0, a1
    return pal


def _alpha_block(alpha: np.ndarray) -> np.ndarray:
    n = len(alpha)
    a = alpha.astype(np.int64)
    amin, amax = a.min(1), a.max(1)
    inner = (a > 0) & (a < 255)
    has_inner = inner.any(1)
    imin = np.where(has_inner, np.where(inner, a, 255).min(1), amin)
    imax = np.where(has_inner, np.where(inner, a, 0).max(1), amax)
    cands = [(amax, amin), (imin, imax)]
    for d0, d1 in ((1, 0), (0, 1), (1, 1), (-1, 0), (0, -1)):
        cands.append((np.clip(amax + d0, 0, 255), np.clip(amin - d1, 0, 255)))
    best_err = np.full(n, np.iinfo(np.int64).max)
    best_a0 = np.zeros(n, np.int64)
    best_a1 = np.zeros(n, np.int64)
    best_idx = np.zeros((n, 16), np.int64)
    for a0, a1 in cands:
        pal = _alpha_palette(a0, a1)
        d = np.abs(a[:, :, None] - pal[:, None, :])
        idx = d.argmin(2)
        err = (np.take_along_axis(d, idx[..., None], 2)[..., 0] ** 2).sum(1)
        better = err < best_err
        best_err = np.where(better, err, best_err)
        best_a0, best_a1 = _keep(better, a0, best_a0), _keep(better, a1, best_a1)
        best_idx = _keep(better, idx, best_idx)
    bits = (best_idx << (3 * np.arange(16, dtype=np.int64))).sum(1)
    out = np.zeros((n, 8), np.uint8)
    out[:, 0], out[:, 1] = best_a0, best_a1
    for k in range(6):
        out[:, 2 + k] = (bits >> (8 * k)) & 255
    return out


def encode_bc1(blocks: np.ndarray) -> np.ndarray:
    out = np.zeros((len(blocks), 8), np.uint8)
    for s, e in _chunks(len(blocks)):
        b = blocks[s:e]
        out[s:e] = _color_block(b, (b[:, :, 3] < 128).any(1))
    return out


def encode_bc3(blocks: np.ndarray) -> np.ndarray:
    out = np.zeros((len(blocks), 16), np.uint8)
    for s, e in _chunks(len(blocks)):
        b = blocks[s:e]
        out[s:e, :8] = _alpha_block(b[:, :, 3])
        out[s:e, 8:] = _color_block(b, np.zeros(len(b), bool))
    return out


def _q7(v: np.ndarray, p: int) -> np.ndarray:
    return np.clip(np.rint((v - p) / 2.0), 0, 127).astype(np.int64)


def _bc7_palette(q0, p0, q1, p1) -> np.ndarray:
    e0 = (q0 << 1) | p0[:, None]
    e1 = (q1 << 1) | p1[:, None]
    return ((64 - BC7_WEIGHTS4)[None, :, None] * e0[:, None, :]
            + BC7_WEIGHTS4[None, :, None] * e1[:, None, :] + 32) >> 6


def _bc7_mode6(blocks: np.ndarray) -> np.ndarray:
    n = len(blocks)
    pix = blocks.astype(np.float64)
    W = channel_weights(blocks)
    pw = 0.05 + blocks[:, :, 3].astype(np.float64) / 255.0
    visible = blocks[:, :, 3] > 0
    best_err = np.full(n, np.inf)
    best_q0 = np.zeros((n, 4), np.int64)
    best_q1 = np.zeros((n, 4), np.int64)
    best_p0 = np.zeros(n, np.int64)
    best_p1 = np.zeros(n, np.int64)
    best_idx = np.zeros((n, 16), np.int64)
    cands = [_axis_endpoints(pix, pw), _axis_endpoints(pix, np.ones_like(pw))]
    cands += _box_endpoints(pix, visible)
    for it in range(3):
        nxt = []
        for e0, e1 in cands:
            local_err = np.full(n, np.inf)
            local = None
            for p0 in (0, 1):
                for p1 in (0, 1):
                    q0, q1 = _q7(e0, p0), _q7(e1, p1)
                    pv0, pv1 = np.full(n, p0, np.int64), np.full(n, p1, np.int64)
                    pal = _bc7_palette(q0, pv0, q1, pv1).astype(np.float64)
                    idx, err = _nearest(pix, pal, W)
                    better = err < best_err
                    best_err = np.where(better, err, best_err)
                    best_q0, best_q1 = _keep(better, q0, best_q0), _keep(better, q1, best_q1)
                    best_p0, best_p1 = _keep(better, pv0, best_p0), _keep(better, pv1, best_p1)
                    best_idx = _keep(better, idx, best_idx)
                    lb = err < local_err
                    local_err = np.where(lb, err, local_err)
                    cur = ((q0 << 1) | p0, (q1 << 1) | p1, idx)
                    local = cur if local is None else tuple(_keep(lb, c, o) for c, o in zip(cur, local))
            if it < 2 and local is not None:
                e0q, e1q, idx = local
                a = BC7_WEIGHTS4[idx] / 64.0
                nxt.append(_lsq_endpoints(pix, W, a, e0q.astype(np.float64), e1q.astype(np.float64)))
        cands = nxt
    swap = best_idx[:, 0] >= 8
    q0, q1 = _keep(swap, best_q1, best_q0), _keep(swap, best_q0, best_q1)
    p0, p1 = _keep(swap, best_p1, best_p0), _keep(swap, best_p0, best_p1)
    idx = _keep(swap, 15 - best_idx, best_idx)
    return _pack_mode6(q0, q1, p0, p1, idx)


def _pack_mode6(q0, q1, p0, p1, idx) -> np.ndarray:
    n = len(q0)
    words = [np.zeros(n, np.uint64), np.zeros(n, np.uint64)]
    pos = 0

    def put(value, bits: int) -> None:
        nonlocal pos
        v = np.asarray(value).astype(np.uint64)
        word, shift = divmod(pos, 64)
        words[word] |= v << np.uint64(shift)
        if shift + bits > 64:
            words[word + 1] |= v >> np.uint64(64 - shift)
        pos += bits

    put(np.full(n, 1 << 6), 7)
    for c in range(4):
        put(q0[:, c], 7)
        put(q1[:, c], 7)
    put(p0, 1)
    put(p1, 1)
    put(idx[:, 0], 3)
    for i in range(1, 16):
        put(idx[:, i], 4)
    assert pos == 128
    out = np.zeros((n, 16), np.uint8)
    for k in range(16):
        out[:, k] = ((words[k // 8] >> np.uint64(8 * (k % 8))) & np.uint64(255)).astype(np.uint8)
    return out


def encode_bc7(blocks: np.ndarray) -> np.ndarray:
    out = np.zeros((len(blocks), 16), np.uint8)
    for s, e in _chunks(len(blocks)):
        out[s:e] = _bc7_mode6(blocks[s:e])
    return out


ENCODERS = {0x60: (encode_bc1, 8), 0x62: (encode_bc3, 16), 0x66: (encode_bc7, 16)}


def encode_blocks(fmt: int, blocks: np.ndarray) -> np.ndarray:
    if fmt not in ENCODERS:
        raise BcnError("no encoder for G1T format 0x%02x (supported: BC1 0x60, BC3 0x62, "
                       "BC7 0x66)" % fmt)
    blocks = np.asarray(blocks, np.uint8)
    if blocks.ndim != 3 or blocks.shape[1:] != (16, 4):
        raise BcnError("blocks must have shape (N, 16, 4)")
    return ENCODERS[fmt][0](blocks)


def compare(expected: np.ndarray, got: np.ndarray) -> dict:
    a = np.asarray(expected, np.float64)
    b = np.asarray(got, np.float64)
    pa = np.concatenate([a[..., :3] * a[..., 3:] / 255.0, a[..., 3:]], -1)
    pb = np.concatenate([b[..., :3] * b[..., 3:] / 255.0, b[..., 3:]], -1)
    d = pa - pb
    mse = float((d * d).mean()) if d.size else 0.0
    psnr = 99.0 if mse <= 1e-12 else min(99.0, 10.0 * np.log10(255.0 ** 2 / mse))
    return {"psnr": round(psnr, 2), "max_abs": int(np.ceil(np.abs(d).max())) if d.size else 0}
