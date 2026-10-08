"""
A deliberately tiny PDF writer for statements and reports: text lines, a few rules,
multiple A4 pages, the built-in Helvetica fonts. No third-party dependency (a PDF
library is a large attack surface for what is a table of numbers).

    doc = SimplePdf(title="Statement")
    doc.text("SokoPay statement", size=16, bold=True)
    doc.row(["Date", "Details", "In", "Out"], widths=[80, 260, 80, 80], bold=True)
    doc.rule()
    data = doc.render()          # bytes

Text is encoded as WinAnsi (cp1252); characters outside it become "?". The cedi
sign (₵) is not in WinAnsi, so amounts should be written "GHS 12.00".
"""

from __future__ import annotations

PAGE_W, PAGE_H = 595, 842          # A4 in points
MARGIN = 40
LINE = 14


def _esc(s: str) -> str:
    s = s.encode("cp1252", errors="replace").decode("cp1252")
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").replace("\r", " ").replace("\n", " ")


def _width(s: str, size: float) -> float:
    return len(s) * size * 0.5            # Helvetica average glyph width, good enough for clipping


class SimplePdf:
    def __init__(self, title: str = "", footer: str = ""):
        self.title, self.footer = title, footer
        self.pages: list[list[str]] = []
        self.y = 0.0
        self._new_page()

    # --- layout -------------------------------------------------------------------------------
    def _new_page(self) -> None:
        self.pages.append([])
        self.y = PAGE_H - MARGIN

    def _need(self, h: float) -> None:
        if self.y - h < MARGIN + LINE:
            self._new_page()

    def _put(self, x: float, s: str, size: float, bold: bool) -> None:
        font = "F2" if bold else "F1"
        self.pages[-1].append(f"BT /{font} {size:g} Tf {x:.1f} {self.y:.1f} Td ({_esc(s)}) Tj ET")

    def text(self, s: str, *, size: float = 10, bold: bool = False, gap: float = 0) -> None:
        self._need(size + 4 + gap)
        self.y -= gap
        self._put(MARGIN, s, size, bold)
        self.y -= size + 4

    def row(self, cells: list[str], widths: list[float], *, size: float = 9, bold: bool = False,
            right: set[int] | None = None) -> None:
        self._need(LINE)
        x = MARGIN
        for i, (c, w) in enumerate(zip(cells, widths)):
            c = str(c)
            while c and _width(c, size) > w - 4:     # clip to the column
                c = c[:-2] + "…" if len(c) > 2 else ""
            cx = x + w - 4 - _width(c, size) if right and i in right else x
            self._put(cx, c, size, bold)
            x += w
        self.y -= LINE

    def rule(self) -> None:
        self._need(6)
        self.pages[-1].append(f"0.6 G 0.5 w {MARGIN} {self.y + 9:.1f} m {PAGE_W - MARGIN} {self.y + 9:.1f} l S 0 G")
        self.y -= 4

    # --- output -------------------------------------------------------------------------------
    def render(self) -> bytes:
        objs: list[bytes] = []

        def add(b: str | bytes) -> int:
            objs.append(b.encode("latin-1") if isinstance(b, str) else b)
            return len(objs)

        catalog = add("")                     # placeholders, filled below
        pages_id = add("")
        f1 = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        f2 = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        kids = []
        total = len(self.pages)
        for n, ops in enumerate(self.pages, 1):
            foot = []
            label = f"{self.footer}   Page {n} of {total}".strip()
            foot.append(f"BT /F1 7 Tf {MARGIN} 22 Td ({_esc(label)}) Tj ET")
            stream = "\n".join(ops + foot).encode("cp1252", errors="replace")
            content = add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
            kids.append(add(f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
                            f"/Resources << /Font << /F1 {f1} 0 R /F2 {f2} 0 R >> >> /Contents {content} 0 R >>"))
        objs[catalog - 1] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode()
        objs[pages_id - 1] = (f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] "
                              f"/Count {len(kids)} >>").encode()
        info = add(f"<< /Title ({_esc(self.title)}) /Producer (SokoPay) >>")

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(objs, 1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
        for off in offsets:
            out += f"{off:010d} 00000 n \n".encode()
        out += (f"trailer\n<< /Size {len(objs) + 1} /Root {catalog} 0 R /Info {info} 0 R >>\n"
                f"startxref\n{xref}\n%%EOF\n").encode()
        return bytes(out)
