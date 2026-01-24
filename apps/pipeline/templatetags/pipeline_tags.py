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
