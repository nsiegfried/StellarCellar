import io

import segno


def svg(data: str) -> str:
    buf = io.BytesIO()
    segno.make(data, error="q").save(
        buf, kind="svg", scale=4, dark="#000000", light="#ffffff", xmldecl=False
    )
    return buf.getvalue().decode()
