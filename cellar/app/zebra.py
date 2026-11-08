"""Direct label printing to a Zebra printer over the network (ZPL on TCP port 9100)."""
import socket

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


def send(host: str, zpl: str, port: int = 9100, timeout: float = 5.0) -> None:
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall(zpl.encode("utf-8"))


def status(host: str, port: int = 9100, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
