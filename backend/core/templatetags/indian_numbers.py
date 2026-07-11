from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


def _format_indian_number(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float, Decimal)):
        number = value
    else:
        raw = str(value).strip()
        if raw == "":
            return ""
        try:
            number = Decimal(raw.replace(",", ""))
        except (InvalidOperation, ValueError):
            return raw

    is_negative = number < 0
    number = abs(number)
    integer_part = int(number)
    decimal_part = number - integer_part
    integer_text = str(integer_part)

    if len(integer_text) > 3:
        last_three = integer_text[-3:]
        rest = integer_text[:-3]
        grouped = []
        while rest:
            grouped.insert(0, rest[-2:])
            rest = rest[:-2]
        integer_text = ",".join(grouped + [last_three])

    if decimal_part:
        decimal_text = f"{decimal_part:.2f}".split(".")[1].rstrip("0")
        if decimal_text:
            integer_text = f"{integer_text}.{decimal_text}"

    return f"-{integer_text}" if is_negative else integer_text


@register.filter(name="indian")
def indian(value) -> str:
    return _format_indian_number(value)


@register.filter(name="short_indian")
def short_indian(value, lang="en") -> str:
    """Compact rupee display. Pass 'ta' for Tamil units (கோடி/லட்சம்)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float, Decimal)):
        number = value
    else:
        raw = str(value).strip()
        if raw == "":
            return ""
        try:
            number = Decimal(raw.replace(",", ""))
        except (InvalidOperation, ValueError):
            return raw

    is_negative = number < 0
    number = abs(number)
    is_ta = lang == "ta"

    CRORE = 10_000_000
    LAKH = 100_000

    if number >= CRORE:
        shortened = float(number) / CRORE
        text = f"{shortened:.2f}".rstrip("0").rstrip(".")
        result = f"{text} கோடி" if is_ta else f"{text} Cr"
    elif number >= LAKH:
        shortened = float(number) / LAKH
        text = f"{shortened:.1f}".rstrip("0").rstrip(".")
        result = f"{text} லட்சம்" if is_ta else f"{text} L"
    else:
        return _format_indian_number(value)

    return f"-{result}" if is_negative else result


@register.filter(name="short_indian_ta")
def short_indian_ta(value) -> str:
    """Tamil variant: >= 1 Cr shows 'X.XX கோடி', >= 1 L shows 'X.X லட்சம்'."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float, Decimal)):
        number = value
    else:
        raw = str(value).strip()
        if raw == "":
            return ""
        try:
            number = Decimal(raw.replace(",", ""))
        except (InvalidOperation, ValueError):
            return raw

    is_negative = number < 0
    number = abs(number)

    CRORE = 10_000_000
    LAKH = 100_000

    if number >= CRORE:
        shortened = float(number) / CRORE
        text = f"{shortened:.2f}".rstrip("0").rstrip(".")
        result = f"{text} கோடி"
    elif number >= LAKH:
        shortened = float(number) / LAKH
        text = f"{shortened:.1f}".rstrip("0").rstrip(".")
        result = f"{text} லட்சம்"
    else:
        return _format_indian_number(value)

    return f"-{result}" if is_negative else result


@register.filter(name="get_item")
def get_item(mapping, key):
    if mapping is None:
        return ""
    try:
        return mapping.get(key, "")
    except AttributeError:
        return ""
