"""String Expression — convierte palabras en números, evalúa y devuelve el resultado en palabras."""

WORD_TO_DIGIT = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
}

DIGIT_TO_WORD = {v: k for k, v in WORD_TO_DIGIT.items()}

# Palabras más largas primero para evitar coincidencias parciales (ej. "nine" vs "n")
_TOKENS = sorted(
    list(WORD_TO_DIGIT.keys()) + ["plus", "minus"],
    key=len,
    reverse=True,
)


def _tokenize(s: str) -> list[str]:
    """Divide la cadena en dígitos escritos y operadores plus/minus."""
    tokens: list[str] = []
    i = 0
    while i < len(s):
        matched = False
        for word in _TOKENS:
            if s.startswith(word, i):
                tokens.append(word)
                i += len(word)
                matched = True
                break
        if not matched:
            raise ValueError(f"Token no reconocido en posición {i}: {s[i:]!r}")
    return tokens


def _parse_expression(tokens: list[str]) -> tuple[list[int], list[str]]:
    """Convierte tokens en lista de enteros y operadores (+ / -)."""
    numbers: list[int] = []
    operators: list[str] = []
    current_digits: list[str] = []

    def flush_number() -> None:
        if current_digits:
            numbers.append(int("".join(current_digits)))
            current_digits.clear()

    for token in tokens:
        if token in WORD_TO_DIGIT:
            current_digits.append(WORD_TO_DIGIT[token])
        elif token in ("plus", "minus"):
            flush_number()
            operators.append("+" if token == "plus" else "-")
        else:
            raise ValueError(f"Token inesperado: {token}")

    flush_number()
    return numbers, operators


def _evaluate(numbers: list[int], operators: list[str]) -> int:
    """Evalúa de izquierda a derecha (solo + y -)."""
    if not numbers:
        raise ValueError("La expresión no contiene números")
    if len(operators) != len(numbers) - 1:
        raise ValueError("Cantidad de operadores inconsistente con los números")

    result = numbers[0]
    for op, num in zip(operators, numbers[1:]):
        if op == "+":
            result += num
        else:
            result -= num
    return result


def _number_to_words(n: int) -> str:
    """Convierte un entero al formato escrito digit-por-digit."""
    if n < 0:
        return "negative" + _number_to_words(-n)
    return "".join(DIGIT_TO_WORD[d] for d in str(n))


def StringExpression(strParam: str) -> str:
    """
    Lee una cadena con dígitos escritos (zero–nine) y plus/minus,
    evalúa la expresión y devuelve el resultado también en palabras.
    """
    tokens = _tokenize(strParam.lower())
    numbers, operators = _parse_expression(tokens)
    result = _evaluate(numbers, operators)
    return _number_to_words(result)


if __name__ == "__main__":
    examples = [
        "foursixminustwotwoplusonezero",
        "onezeropluseight",
        "oneminusoneone",
    ]
    for expr in examples:
        print(f"{expr} -> {StringExpression(expr)}")
