"""Direct label printing to a Zebra printer over the network (ZPL on TCP port 9100)."""
import socket

import segno

MM_PER_INCH = 25.4


def dots(mm: float, dpi: int) -> int:
    return int(round(mm / MM_PER_INCH * dpi))


def qr_magnification(target_mm: float, dpi: int, modules: int = 29) -> int:
    """Pick the ZPL magnification (dots per module) that gets a 29-module QR closest to target_mm."""
    per_module = dots(target_mm, dpi) / modules
    return max(2, min(10, int(round(per_module))))


def label_zpl(code: str, title: str, payload: str, *, width_mm: float, height_mm: float, dpi: int,
              darkness: int = 20) -> str:
    """One label: QR on the left, code and title on the right. Works from 25 x 13 mm upward."""
    w, h = dots(width_mm, dpi), dots(height_mm, dpi)
    margin = dots(1.0, dpi)
    qr_mm = min(height_mm - 2.0, width_mm * 0.45)
    mag = qr_magnification(qr_mm, dpi)
    qr_px = mag * 29  # 21 modules + 2 x 4 quiet zone modules at ZPL's default
    text_x = margin + qr_px + dots(1.0, dpi)
    text_w = max(w - text_x - margin, dots(6, dpi))
    code_h = max(dots(2.6, dpi), 18)
    title_h = max(dots(2.0, dpi), 14)
    title = (title or "")[:40].replace("^", "").replace("~", "")
    return (
        "^XA"
        f"^PW{w}^LL{h}^LH0,0^MD{max(-30, min(30, darkness - 20))}"
        f"^FO{margin},{max(0, (h - qr_px) // 2)}^BQN,2,{mag}^FDQA,{payload}^FS"
        f"^FO{text_x},{margin}^A0N,{code_h},{code_h}^FB{text_w},1,0,L^FD{code}^FS"
        f"^FO{text_x},{margin + code_h + dots(0.8, dpi)}^A0N,{title_h},{title_h}^FB{text_w},2,0,L^FD{title}^FS"
        "^XZ"
    )


def qr_graphic(payload: str, size_px: int, border: int = 2) -> tuple:
    """Render a QR as a ZPL ^GFA bitmap of roughly size_px, so it can be any size (ZPL's ^BQ caps at
    magnification 10). Returns (zpl_fragment, actual_px)."""
    q = segno.make(payload, error="q", micro=False)
    matrix = [list(row) for row in q.matrix]
    n = len(matrix) + border * 2
    scale = max(1, size_px // n)
    px = n * scale
    row_bytes = (px + 7) // 8
    rows = []
    for ry in range(n):
        my = ry - border
        bits = []
        for rx in range(n):
            mx = rx - border
            dark = 0 <= my < len(matrix) and 0 <= mx < len(matrix) and matrix[my][mx]
            bits.extend([1 if dark else 0] * scale)
        bits.extend([0] * (row_bytes * 8 - len(bits)))
        row = bytes(int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8))
        rows.append(row.hex().upper())
    data = "".join(row for row in rows for _ in range(scale))
    total = row_bytes * px
    return f"^GFA,{total},{total},{row_bytes},{data}", px


def right_half_zpl(code: str, title: str, payload: str, *, width_mm: float, height_mm: float, dpi: int,
                   darkness: int = 20, subtitle: str = "", qr_scale: float = 1.0, fields: dict = None) -> str:
    """Large-format layout.
    Right half: QR, tiny code, producer and wine name, vintage large, drink window, footer (region, score, held for).
    Left half: vintage and drink window in big type so the label reads from across the fridge."""
    f = fields or {}
    w, h = dots(width_mm, dpi), dots(height_mm, dpi)
    m = dots(2.0, dpi)
    half = w // 2
    col_x, col_w = half + m, w - half - 2 * m
    # type sizes (mm)
    code_h = dots(2.0, dpi)
    name_h = dots(4.2, dpi)
    vint_h = dots(7.0, dpi)
    win_h = dots(3.6, dpi)
    foot_h = dots(3.0, dpi)
    gap = dots(1.2, dpi)
    text_block = code_h + gap + name_h * 2 + gap + vint_h + gap + win_h + gap + foot_h + gap
    qr_target = int(min(col_w, h - 2 * m - text_block) * max(0.3, min(1.0, qr_scale)))
    graphic, qr_px = qr_graphic(payload, max(qr_target, dots(14, dpi)))
    qr_x = col_x + (col_w - qr_px) // 2

    def clean(s, n):
        return (s or "")[:n].replace("^", "").replace("~", "")

    name = clean(title, 70)
    vintage = clean(f.get("vintage") or "", 8)
    window = clean(f.get("window") or "", 30)
    footer = clean(f.get("footer") or subtitle, 50)

    y = m
    out = ["^XA", f"^PW{w}^LL{h}^LH0,0^MD{max(-30, min(30, darkness - 20))}"]
    out.append(f"^FO{qr_x},{y}{graphic}^FS"); y += qr_px
    out.append(f"^FO{col_x},{y}^A0N,{code_h},{code_h}^FB{col_w},1,0,C^FD{code}^FS"); y += code_h + gap
    out.append(f"^FO{col_x},{y}^A0N,{name_h},{name_h}^FB{col_w},2,0,C^FD{name}^FS"); y += name_h * 2 + gap
    if vintage:
        out.append(f"^FO{col_x},{y}^A0N,{vint_h},{vint_h}^FB{col_w},1,0,C^FD{vintage}^FS")
    y += vint_h + gap
    if window:
        out.append(f"^FO{col_x},{y}^A0N,{win_h},{win_h}^FB{col_w},1,0,C^FD{window}^FS")
    y += win_h + gap
    if footer:
        out.append(f"^FO{col_x},{y}^A0N,{foot_h},{foot_h}^FB{col_w},1,0,C^FD{footer}^FS")

    # left half: big vintage and window, vertically centred
    lx, lw = m, half - 2 * m
    big_v = dots(16, dpi)
    big_w = dots(5.0, dpi)
    block = (big_v if vintage else 0) + (gap * 2 + big_w if window else 0)
    ly = max(m, (h - block) // 2)
    if vintage:
        out.append(f"^FO{lx},{ly}^A0N,{big_v},{big_v}^FB{lw},1,0,C^FD{vintage}^FS"); ly += big_v + gap * 2
    if window:
        out.append(f"^FO{lx},{ly}^A0N,{big_w},{big_w}^FB{lw},2,0,C^FD{window}^FS")
    # faint divider so the two halves read as a pair
    out.append(f"^FO{half - 1},{m}^GB2,{h - 2 * m},2^FS")
    out.append("^XZ")
    return "".join(out)


def render(layout: str, code: str, title: str, payload: str, *, subtitle: str = "", qr_scale: float = 1.0,
           fields: dict = None, **kw) -> str:
    if layout == "right_half":
        return right_half_zpl(code, title, payload, subtitle=subtitle, qr_scale=qr_scale, fields=fields, **kw)
    return label_zpl(code, title, payload, **kw)


def send(host: str, zpl: str, port: int = 9100, timeout: float = 5.0) -> None:
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall(zpl.encode("utf-8"))


def status(host: str, port: int = 9100, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
