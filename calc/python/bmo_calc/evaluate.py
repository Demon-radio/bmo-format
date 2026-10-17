"""Formula evaluator: Excel-compatible value semantics over BMO sheets.
Blank -> 0 in arithmetic, bool 1/0, numeric text coerced, else #VALUE!.
Type order for comparisons: number < text < bool(FALSE<TRUE).
Errors propagate (incl. through taken IF branches); AND/OR short-circuit.
Ranges flatten row-major; cycles -> #CYCLE! via evaluation guard.
"""
import math
import re

from .formula import FErr, parse

def _col_row(addr: str):
    a = addr.replace("$", "")
    m = re.match(r"^([A-Za-z]{1,3})([0-9]{1,7})$", a)
    col = 0
    for ch in m.group(1).upper():
        col = col * 26 + (ord(ch) - 64)
    return col, int(m.group(2))

def _norm(addr: str) -> str:
    return addr.replace("$", "").upper()

def _expand(a: str, b: str):
    c1, r1 = _col_row(a)
    c2, r2 = _col_row(b)
    if c1 != c2 or abs(r2 - r1) > 100000:
        # multi-column or huge: enumerate bounded box (cap 100k)
        out = []
        for cc in range(min(c1, c2), max(c1, c2) + 1):
            letters = ""
            n = cc
            while n:
                n, r = divmod(n - 1, 26)
                letters = chr(65 + r) + letters
            for rr in range(min(r1, r2), max(r1, r2) + 1):
                out.append(f"{letters}{rr}")
                if len(out) >= 100000:
                    return out
        return out
    lo, hi = min(r1, r2), max(r1, r2)
    letters = ""
    n = c1
    while n:
        n, r = divmod(n - 1, 26)
        letters = chr(65 + r) + letters
    return [f"{letters}{r}" for r in range(lo, hi + 1)]

def _num(v):
    """Coerce to number (Excel rules) or raise FErr(#VALUE!)."""
    if v is None:
        return 0
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        s = v.strip()
        if re.match(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$", s):
            try:
                return int(s) if re.match(r"^[+-]?\d+$", s) else float(s)
            except ValueError:
                pass
        raise FErr("#VALUE!")
    raise FErr("#VALUE!")

def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)

def _order(v):
    if isinstance(v, bool):
        return (2, 1 if v else 0)
    if isinstance(v, (int, float)):
        return (0, v)
    return (1, str(v).lower())

class Evaluator:
    def __init__(self, sheets: dict, cur_sheet: str):
        """sheets: {name: {ADDR: cell_dict}}. Cells hold v/err/f (raw)."""
        self.sheets = sheets
        self.cur = cur_sheet
        self.cache = {}      # (sheet, addr) -> computed value
        self.stack = []      # evaluation guard for cycles
        self.funcs = {
            "SUM": self.f_sum, "AVERAGE": self.f_average, "MIN": self.f_min,
            "MAX": self.f_max, "COUNT": self.f_count, "COUNTA": self.f_counta,
            "ABS": self.f_abs, "ROUND": self.f_round, "INT": self.f_int,
            "MOD": self.f_mod, "POWER": self.f_power, "SQRT": self.f_sqrt,
            "EXP": self.f_exp, "LN": self.f_ln, "LOG10": self.f_log10,
            "LEN": self.f_len, "UPPER": self.f_upper, "LOWER": self.f_lower,
            "TRIM": self.f_trim, "IF": self.f_if, "AND": self.f_and,
            "OR": self.f_or, "NOT": self.f_not,
        }

    # -- cell access --
    def cell_value(self, sheet, addr):
        sheet = sheet or self.cur
        key = (sheet, _norm(addr))
        if key in self.cache:
            return self.cache[key]
        if key in self.stack:
            raise FErr("#CYCLE!")
        cells = self.sheets.get(sheet, {})
        cell = cells.get(_norm(addr))
        if cell is None:
            return None  # blank
        if "err" in cell:
            raise FErr(cell["err"])
        if cell.get("f"):
            self.stack.append(key)
            try:
                v = self.ev(parse(cell["f"]))
            finally:
                self.stack.pop()
            self.cache[key] = v
            return v
        return cell.get("v")

    # -- eval --
    def ev(self, node):
        k = node[0]
        if k == "num":
            return node[1]
        if k == "str":
            return node[1]
        if k == "bool":
            return node[1]
        if k == "ref":
            _, sheet, addr = node
            return self.cell_value(sheet, addr)
        if k == "range":
            raise FErr("#VALUE!")
        if k == "call":
            _, name, args = node
            fn = self.funcs.get(name)
            if fn is None:
                raise FErr("#NAME?")
            return fn(args)
        if k == "bin":
            _, op, l, r = node
            return self.binop(op, self.ev(l), self.ev(r))
        if k == "un":
            _, op, x = node
            v = self.ev(x)
            return _num(v) if op == "+" else -_num(v)
        if k == "pct":
            v = self.ev(node[1])
            if isinstance(v, str):
                raise FErr("#VALUE!")
            return _num(v) / 100
        raise FErr("#NAME?")

    def binop(self, op, l, r):
        if op == "&":
            return _text(l) + _text(r)
        if op in ("=", "<>", "<", ">", "<=", ">="):
            a, b = _order(l), _order(r)
            if op == "=":
                return a == b
            if op == "<>":
                return a != b
            return {"<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]
        l, r = _num(l), _num(r)
        if op == "+":
            return l + r
        if op == "-":
            return l - r
        if op == "*":
            return l * r
        if op == "/":
            if r == 0:
                raise FErr("#DIV/0!")
            return l / r
        if op == "^":
            try:
                v = l ** r
            except (OverflowError, ValueError):
                raise FErr("#NUM!")
            if isinstance(v, complex):
                raise FErr("#NUM!")
            return v
        raise FErr("#NAME?")

    # -- ranges --
    def range_values(self, sheet, a, b):
        sheet = sheet or self.cur
        return [self.cell_value(sheet, x) for x in _expand(_norm(a), _norm(b))]

    def _args_flat(self, args):
        out = []
        for a in args:
            if a[0] == "range":
                _, sheet, x, y = a
                out.extend(self.range_values(sheet, x, y))
            else:
                out.append(self.ev(a))
        return out

    def _numbers(self, args, skip_text=True):
        vals = []
        for v in self._args_flat(args):
            if v is None:
                continue
            if isinstance(v, bool):
                vals.append(1 if v else 0)
            elif isinstance(v, (int, float)):
                vals.append(v)
            elif not skip_text:
                vals.append(_num(v))
        return vals

    # -- functions --
    def f_sum(self, args):
        return sum(self._numbers(args), 0)

    def f_average(self, args):
        v = self._numbers(args)
        if not v:
            raise FErr("#DIV/0!")
        return sum(v) / len(v)

    def f_min(self, args):
        v = self._numbers(args)
        if not v:
            return 0
        return min(v)

    def f_max(self, args):
        v = self._numbers(args)
        if not v:
            return 0
        return max(v)

    def f_count(self, args):
        return len(self._numbers(args))

    def f_counta(self, args):
        return sum(1 for v in self._args_flat(args) if v is not None and v != "")

    def _one(self, args, n=1):
        if len(args) != n:
            raise FErr("#NAME?")
        return self.ev(args[0])

    def f_abs(self, args):
        return abs(_num(self._one(args)))

    def f_round(self, args):
        if len(args) != 2:
            raise FErr("#NAME?")
        # Excel: half away from zero on DECIMAL value (not binary float).
        from decimal import Decimal, ROUND_HALF_UP
        x, n = self.ev(args[0]), self.ev(args[1])
        if isinstance(x, str) or isinstance(n, str):
            raise FErr("#VALUE!")
        xv, nv = _num(x), int(_num(n))
        q = Decimal(1).scaleb(-nv)
        return float(Decimal(str(xv)).quantize(q, rounding=ROUND_HALF_UP))

    def f_int(self, args):
        return math.floor(_num(self._one(args)))

    def f_mod(self, args):
        if len(args) != 2:
            raise FErr("#NAME?")
        n, d = _num(self.ev(args[0])), _num(self.ev(args[1]))
        if d == 0:
            raise FErr("#DIV/0!")
        return n % d  # Python % follows the divisor sign, like Excel MOD

    def f_power(self, args):
        if len(args) != 2:
            raise FErr("#NAME?")
        return self.binop("^", self.ev(args[0]), self.ev(args[1]))

    def f_sqrt(self, args):
        v = _num(self._one(args))
        if v < 0:
            raise FErr("#NUM!")
        return math.sqrt(v)

    def f_exp(self, args):
        return math.exp(_num(self._one(args)))

    def f_ln(self, args):
        v = _num(self._one(args))
        if v <= 0:
            raise FErr("#NUM!")
        return math.log(v)

    def f_log10(self, args):
        v = _num(self._one(args))
        if v <= 0:
            raise FErr("#NUM!")
        return math.log10(v)

    def f_len(self, args):
        return len(_text(self._one(args)))

    def f_upper(self, args):
        return _text(self._one(args)).upper()

    def f_lower(self, args):
        return _text(self._one(args)).lower()

    def f_trim(self, args):
        return re.sub(r"\s+", " ", _text(self._one(args))).strip()

    def _truthy(self, v) -> bool:
        if isinstance(v, bool):
            return v
        if v is None:
            return False
        if isinstance(v, (int, float)):
            return v != 0
        if isinstance(v, str):
            s = v.strip().upper()
            if s == "TRUE":
                return True
            if s == "FALSE":
                return False
            raise FErr("#VALUE!")
        raise FErr("#VALUE!")

    def f_if(self, args):
        if len(args) not in (2, 3):
            raise FErr("#NAME?")
        cond = self.ev(args[0])
        if self._truthy(cond):
            return self.ev(args[1])
        return self.ev(args[2]) if len(args) == 3 else False

    def f_and(self, args):
        for a in args:
            if not self._truthy(self.ev(a)):
                return False
        return True

    def f_or(self, args):
        for a in args:
            if self._truthy(self.ev(a)):
                return True
        return False

    def f_not(self, args):
        return not self._truthy(self._one(args))


def evaluate_sheet(cells: dict, sheets: dict = None, name: str = "Sheet1") -> dict:
    """cells: {ADDR: cell}. Returns {ADDR: computed_value_or_err_string}."""
    sheets = dict(sheets or {})
    sheets[name] = cells
    ev = Evaluator(sheets, name)
    out = {}
    for addr, cell in cells.items():
        try:
            if "err" in cell:
                out[addr] = cell["err"]
            elif cell.get("f"):
                out[addr] = ev.cell_value(name, addr)
            else:
                out[addr] = cell.get("v")
        except FErr as e:
            out[addr] = e.code
    return out
