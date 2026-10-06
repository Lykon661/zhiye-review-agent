"""Convert common Office Math (OMML) structures without a model call."""

MATH_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
SYMBOLS = {
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon",
    "θ": "theta", "λ": "lambda", "μ": "mu", "π": "pi", "σ": "sigma",
    "φ": "phi", "ω": "omega", "Δ": "Delta", "Σ": "Sigma", "Ω": "Omega",
    "ζ": "zeta", "η": "eta", "ι": "iota", "κ": "kappa", "ν": "nu", "ξ": "xi",
    "ρ": "rho", "τ": "tau", "υ": "upsilon", "χ": "chi", "ψ": "psi", "ϕ": "varphi",
    "Γ": "Gamma", "Θ": "Theta", "Λ": "Lambda", "Π": "Pi", "Φ": "Phi", "Ψ": "Psi",
    "∞": "infty", "∂": "partial", "∇": "nabla", "∑": "sum", "∏": "prod",
    "∫": "int", "∬": "iint", "∮": "oint", "√": "surd", "×": "times",
    "÷": "div", "·": "cdot", "≤": "le", "≥": "ge", "≠": "ne",
    "≈": "approx", "∈": "in", "∉": "notin", "→": "to", "⇒": "Rightarrow",
    "∀": "forall", "∃": "exists", "∧": "land", "∨": "lor", "±": "pm",
}


def math_text(value):
    return "".join("\\" + SYMBOLS[c] + " " if c in SYMBOLS else c for c in value).replace("−", "-")


def office_formulas(element):
    results = []
    for equation in element.iter("{" + MATH_NS + "}oMath"):
        unsupported = set()

        def tag(node):
            return node.tag.rsplit("}", 1)[-1] if isinstance(node.tag, str) else ""

        def prop(node, name, default=""):
            for child in node:
                if tag(child).endswith("Pr"):
                    item = child.find("{" + MATH_NS + "}" + name)
                    if item is not None:
                        return item.get("{" + MATH_NS + "}val", default)
            return default

        def part(node, name):
            child = node.find("{" + MATH_NS + "}" + name)
            return convert(child) if child is not None else ""

        def convert(node):
            name = tag(node)
            if name.endswith("Pr"):
                return ""
            if name == "t":
                return math_text(node.text or "")
            if name == "r":
                content = "".join(convert(child) for child in node)
                style = prop(node, "sty")
                return "\\boldsymbol{" + content + "}" if style in {"b", "bi"} else content
            if name in {"oMath", "oMathPara", "e", "num", "den", "sub", "sup", "deg", "fName", "lim"}:
                return "".join(convert(child) for child in node)
            if name == "f":
                numerator, denominator = part(node, "num"), part(node, "den")
                if prop(node, "type") == "noBar":
                    return "\\binom{" + numerator + "}{" + denominator + "}"
                return "\\frac{" + numerator + "}{" + denominator + "}"
            if name in {"sSub", "sSup", "sSubSup", "sPre"}:
                base = "{" + part(node, "e") + "}"
                scripts = ("_{" + part(node, "sub") + "}" if name != "sSup" else "")
                scripts += ("^{" + part(node, "sup") + "}" if name != "sSub" else "")
                return "{}" + scripts + base if name == "sPre" else base + scripts
            if name == "rad":
                degree = part(node, "deg") if prop(node, "degHide") not in {"1", "true"} else ""
                return "\\sqrt" + ("[" + degree + "]" if degree else "") + "{" + part(node, "e") + "}"
            if name == "nary":
                value = math_text(prop(node, "chr", "∫"))
                for script, key in (("_", "sub"), ("^", "sup")):
                    content = part(node, key)
                    if content and prop(node, key + "Hide") not in {"1", "true"}:
                        value += script + "{" + content + "}"
                return value + " " + part(node, "e")
            if name == "d":
                delimiters = {"{": "\\{", "}": "\\}", "": ".", "‖": "\\Vert", "〈": "\\langle", "〉": "\\rangle"}
                left, right = prop(node, "begChr", "("), prop(node, "endChr", ")")
                separator = math_text(prop(node, "sepChr", "|"))
                contents = separator.join(convert(child) for child in node if tag(child) == "e")
                return "\\left" + delimiters.get(left, left) + " " + contents + " \\right" + delimiters.get(right, right)
            if name == "m":
                rows = [" & ".join(convert(cell) for cell in row if tag(cell) == "e") for row in node if tag(row) == "mr"]
                return "\\begin{matrix}" + " \\\\ ".join(rows) + "\\end{matrix}"
            if name == "eqArr":
                return "\\begin{aligned}" + " \\\\ ".join(convert(child) for child in node if tag(child) == "e") + "\\end{aligned}"
            if name == "func":
                return part(node, "fName") + " " + part(node, "e")
            if name in {"limLow", "limUpp"}:
                return "{" + part(node, "e") + "}" + ("_" if name == "limLow" else "^") + "{" + part(node, "lim") + "}"
            if name == "bar":
                return ("\\underline" if prop(node, "pos") == "bot" else "\\overline") + "{" + part(node, "e") + "}"
            if name == "acc":
                accent = {"̂": "hat", "̅": "bar", "⃗": "vec", "̃": "tilde", "̇": "dot", "̈": "ddot"}.get(prop(node, "chr", "̂"))
                if accent:
                    return "\\" + accent + "{" + part(node, "e") + "}"
            # Preserve legible content, but explicitly flag unknown semantic structures.
            unsupported.add(name)
            return "".join(convert(child) for child in node)

        latex = convert(equation).strip()
        if latex:
            results.append({"latex": latex, "unsupported": sorted(unsupported)})
    return results
