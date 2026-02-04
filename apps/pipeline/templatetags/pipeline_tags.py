from django import template

register = template.Library()


@register.filter
def dict_get(dictionary, key):
    """딕셔너리에서 키로 값 가져오기"""
    if dictionary is None:
        return None
    return dictionary.get(key)


@register.filter
def divisibleby(value, divisor):
    """나눗셈 (파일 크기 변환용)"""
    try:
        return value / divisor
    except (ValueError, TypeError, ZeroDivisionError):
        return value


@register.filter
def format_number(value):
    """숫자를 K, M 단위로 포맷 (예: 1900 -> 1.9K)"""
    try:
        value = int(value)
        if value >= 1000000:
            return f'{value/1000000:.1f}M'
        elif value >= 1000:
            return f'{value/1000:.1f}K'
        else:
            return str(value)
    except (ValueError, TypeError):
        return value
