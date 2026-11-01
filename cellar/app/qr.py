import io

import segno


def svg(data: str, border: int = 4) -> str:
    """QR as inline SVG. Error correction Q survives scuffs and condensation.
    A bare 7-character code fits QR version 1 (21 modules), the smallest there is."""
    buf = io.BytesIO()
    segno.make(data, error="q", micro=False).save(
        buf, kind="svg", scale=4, border=border, dark="#000000", light="#ffffff", xmldecl=False
    )
    return buf.getvalue().decode()
