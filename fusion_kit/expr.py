"""Expression-carrying values for Fusion parametric scripts.

Ex(e, v, const, unit) pairs a Fusion expression string with its float value.
unit: "mm" (length, v in mm) | "deg" (angle, v in radians, literal written in deg) | "" (unitless).
All arithmetic preserves the expression so it can be fed to ValueInput.createByString.
Const folding keeps the unit; mixing units in +/- raises ValueError.
Pure Python — no adsk import.
"""

import math
import re


def _lit(v: float, unit: str) -> str:
    """Literal expression for value v in given unit."""
    if unit == "deg":
        return f"{round(math.degrees(v), 6)} deg"
    if unit == "":
        return f"{round(v, 6)}"
    return f"{round(v, 6)} {unit}"


class Ex:
    """Value with its Fusion expression. Float operands of +/- are treated as mm."""

    def __init__(self, e: str, v: float, const: bool = False, unit: str = "mm") -> None:
        self.e: str = e  # Fusion expression string
        self.v: float = v  # numeric value (mm, radians, or unitless)
        self.const: bool = const  # True -> literal, folds with other literals
        self.unit: str = unit

    @staticmethod
    def mm(v: float) -> "Ex":
        """Literal mm value."""
        return Ex(_lit(v, "mm"), v, True, "mm")

    @staticmethod
    def _w(o: "Ex | float") -> "Ex":
        return o if isinstance(o, Ex) else Ex.mm(o)

    def _same_unit(self, o: "Ex") -> None:
        if self.unit != o.unit:
            raise ValueError(f"unit mismatch: {self.unit!r} vs {o.unit!r} ({self.e} / {o.e})")

    def __add__(self, o: "Ex | float") -> "Ex":
        o = Ex._w(o)
        self._same_unit(o)
        v = self.v + o.v
        if self.const and o.const:
            return Ex(_lit(v, self.unit), v, True, self.unit)
        return Ex(f"({self.e} + {o.e})", v, False, self.unit)

    __radd__ = __add__

    def __sub__(self, o: "Ex | float") -> "Ex":
        o = Ex._w(o)
        self._same_unit(o)
        v = self.v - o.v
        if self.const and o.const:
            return Ex(_lit(v, self.unit), v, True, self.unit)
        return Ex(f"({self.e} - {o.e})", v, False, self.unit)

    def __rsub__(self, o: "Ex | float") -> "Ex":
        return Ex._w(o) - self

    def __neg__(self) -> "Ex":
        if self.const:
            return Ex(_lit(-self.v, self.unit), -self.v, True, self.unit)
        return Ex(f"-({self.e})", -self.v, False, self.unit)

    def __mul__(self, k: "Ex | float") -> "Ex":
        if isinstance(k, Ex):  # e.g. length * cos(angle); units multiply
            unit = "*".join(u for u in (self.unit, k.unit) if u)
            return Ex(f"({self.e}) * ({k.e})", self.v * k.v, False, unit)
        v = self.v * k
        if self.const:
            return Ex(_lit(v, self.unit), v, True, self.unit)
        return Ex(f"({self.e}) * {k}", v, False, self.unit)

    __rmul__ = __mul__

    def __truediv__(self, k: float) -> "Ex":
        v = self.v / k
        if self.const:
            return Ex(_lit(v, self.unit), v, True, self.unit)
        return Ex(f"({self.e}) / {k}", v, False, self.unit)

    def __repr__(self) -> str:
        return f"Ex({self.e!r}, {self.v}, unit={self.unit!r})"


def trig(fn: str, angle: Ex) -> Ex:
    """sin/cos/tan of an Ex angle (radians). Result is unitless Ex."""
    return Ex(f"{fn}({angle.e})", getattr(math, fn)(angle.v), False, "")


def deg(d: float) -> Ex:
    """Literal degree value as Ex (value in radians, expression in deg)."""
    return Ex(f"{d} deg", math.radians(d), True, "deg")


def near(a: float, b: float) -> bool:
    """Tolerance comparison (1e-3)."""
    return abs(a - b) < 1e-3


def as_str(x: str | Ex) -> str:
    """Fusion expression string from a str or Ex (for ValueInput.createByString)."""
    return x.e if isinstance(x, Ex) else x


_FUNCS: dict[str, object] = {
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "sqrt": math.sqrt,
    "abs": abs,
    "floor": math.floor,
    "ceil": math.ceil,
    "max": max,
    "min": min,
    "round": lambda x: math.floor(x + 0.5),  # Fusion round(2.5) = 3 (measured), Python rounds half to even
}
# Fusion unit suffix -> mm factor (lengths). deg -> radians, rad -> as is. "mm" listed before "m".
_LENGTH_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "ft": 304.8}
_UNIT_RE = re.compile(r"(\d+(?:\.\d*)?|\.\d+)\s*(mm|cm|m|in|ft|deg|rad)\b")


def _unit_literal(m: re.Match[str]) -> str:
    num, unit = m.group(1), m.group(2)
    if unit == "deg":
        return f"({num} * _DEG)"
    if unit == "rad":
        return f"({num})"
    return f"({num} * {_LENGTH_MM[unit]})"


def evaluate(expr: str, names: dict[str, float]) -> float:
    """Offline value of a Fusion expression. Lengths in mm, angles in radians (as snapshot_geo).

    Supports + - * / ( ), ';' argument separator, '<num> mm|cm|m|in|ft' (-> mm), '<num> deg|rad' (-> radians)
    and the functions in _FUNCS.
    ponytail: eval on our own expression strings (trusted, offline tests / initial sketch geometry only).
    """
    py = _UNIT_RE.sub(_unit_literal, expr).replace(";", ",")
    return float(eval(py, {"__builtins__": {}, "_DEG": math.pi / 180, **_FUNCS}, dict(names)))


def derive(spec: dict[str, tuple[str, str, str]], names: dict[str, float]) -> dict[str, float]:
    """Evaluate a params spec (name -> (expr, units, comment)) in order on top of names. Returns names + spec."""
    out = dict(names)
    for name, (e, _, _) in spec.items():
        out[name] = evaluate(e, out)
    return out
