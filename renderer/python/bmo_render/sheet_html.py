"""BMO workbook -> HTML (reference table renderer v0.10).
Values shown as stored; formulas exposed via data-f; no evaluation here
(calc is a domain engine, RFC-0005 §1). Merges -> colspan/rowspan.
"""
import re

def _esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def _split(addr):
    m = re.match(r"^([A-Za-z]{1,3})([0-9]+)$", addr)
    col = 0
    for ch in m.group(1).upper():
        col = col * 26 + (ord(ch) - 64)
    return col, int(m.group(2))

def render_workbook(model, title="BMO"):
    by_id = model["by_id"]
    out = [f"<html><head><meta charset=\"utf-8\"><title>{_esc(title)}</title></head><body>"]
    out.append("<!-- BMO reference renderer v0.10: values as stored, no calc -->")
    for e in model["envs"]:
        if e["kind"] != "sheet":
            continue
        body = e["body"]
        cells = body.get("cells", {})
        grid = {}
        maxr = maxc = 0
        for addr, cell in cells.items():
            c, r = _split(addr)
            grid[(r, c)] = (addr, cell)
            maxr, maxc = max(maxr, r), max(maxc, c)
        covered = set()
        spans = {}
        for mg in body.get("merges", []):
            r1, c1, r2, c2 = mg
            spans[(r1 + 1, c1 + 1)] = (r2 - r1 + 1, c2 - c1 + 1)
            for rr in range(r1 + 1, r2 + 2):
                for cc in range(c1 + 1, c2 + 2):
                    if (rr, cc) != (r1 + 1, c1 + 1):
                        covered.add((rr, cc))
        out.append(f"<h2>{_esc(body.get('name', ''))}</h2><table border=\"1\">")
        for r in range(1, maxr + 1):
            out.append("<tr>")
            for c in range(1, maxc + 1):
                if (r, c) in covered:
                    continue
                td = ""
                if (r, c) in spans:
                    rs, cs = spans[(r, c)]
                    td = f" rowspan=\"{rs}\" colspan=\"{cs}\"" if (rs, cs) != (1, 1) else ""
                if (r, c) in grid:
                    _, cell = grid[(r, c)]
                    out.append(_cell_html(cell, td))
                else:
                    out.append(f"<td{td}></td>")
            out.append("</tr>")
        out.append("</table>")
    out.append("</body></html>")
    return "".join(out)

def _cell_html(cell, td_extra):
    if "err" in cell:
        return f"<td{td_extra} data-err=\"{_esc(cell['err'])}\">{_esc(cell['err'])}</td>"
    v = cell.get("v")
    extra = td_extra
    if cell.get("f"):
        extra += f" data-f=\"{_esc(cell['f'])}\""
    if v is None:
        return f"<td{extra}></td>"
    if isinstance(v, bool):
        return f"<td{extra}>{'TRUE' if v else 'FALSE'}</td>"
    return f"<td{extra}>{_esc(str(v))}</td>"
