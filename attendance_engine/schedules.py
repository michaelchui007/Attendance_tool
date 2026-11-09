"""校验独立标准工时及默认、日期段、单日作息。"""
from __future__ import annotations

from datetime import date, datetime, time

DEFAULT_SCHEDULE = {
    'workdayStart': '09:00', 'workdayEnd': '18:30',
    'lunchStart': '12:00', 'lunchEnd': '13:30',
}


def minute_value(value: object, label: str, *, allow_zero: bool = False) -> int:
    """校验分钟整数，不接受布尔值或隐式取整。"""
    if type(value) is not int or not (0 if allow_zero else 1) <= value <= 1440:
        raise ValueError(f'{label}须为{0 if allow_zero else 1}～1440的整数分钟')
    return value


def clock(value: object) -> time:
    """严格解析分钟级 HH:mm，避免宽松解析产生不一致配置。"""
    if not isinstance(value, str):
        raise ValueError('作息时间须为 HH:mm')
    try:
        parsed = datetime.strptime(value, '%H:%M').time()
    except ValueError as exc:
        raise ValueError('作息时间须为 HH:mm') from exc
    if parsed.strftime('%H:%M') != value:
        raise ValueError('作息时间须为 HH:mm')
    return parsed


def validate_schedule(value: dict) -> dict:
    """校验完整白班作息，返回不包含可变引用的规范快照。"""
    if not isinstance(value, dict):
        raise ValueError('作息方案必须为对象')
    result = {key: value.get(key) for key in DEFAULT_SCHEDULE}
    start, end = clock(result['workdayStart']), clock(result['workdayEnd'])
    if start >= end:
        raise ValueError('正常下班必须晚于上班，当前仅支持同日作息')
    lunch_start, lunch_end = result['lunchStart'], result['lunchEnd']
    pause = 0
    if (lunch_start is None) != (lunch_end is None):
        raise ValueError('无午休时开始与结束均须为空')
    if lunch_start is not None:
        left, right = clock(lunch_start), clock(lunch_end)
        if not start <= left < right <= end:
            raise ValueError('午休须在正常上下班范围内且结束晚于开始')
        pause = (right.hour * 60 + right.minute) - (left.hour * 60 + left.minute)
    net = (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute) - pause
    if net <= 0:
        raise ValueError('正常工作时段净时长必须大于零')
    result['netScheduleMinutes'] = net
    return result


def normalize_settings(settings: dict | None, lunch_start: str | None = None, lunch_end: str | None = None) -> dict:
    """校验系统规则，标准时长始终独立，兼容旧午休参数。

    Args:
        settings: 新协议系统配置，省略字段使用默认值。
        lunch_start: 旧协议午休开始。
        lunch_end: 旧协议午休结束。

    Returns:
        已校验的配置及无重叠日期段。
    """
    if settings is not None and not isinstance(settings, dict):
        raise ValueError('系统配置必须为对象')
    raw = dict(settings or {})
    base = {**DEFAULT_SCHEDULE, **{key: raw[key] for key in DEFAULT_SCHEDULE if key in raw}}
    if settings is None and (lunch_start is not None or lunch_end is not None):
        base['lunchStart'] = lunch_start or '12:00'
        base['lunchEnd'] = lunch_end or '13:30'
    result = validate_schedule(base)
    result.update(standardMinutes=minute_value(raw.get('standardMinutes', 480), '标准工时'),
                  dayBoundary='04:00', locationBenefitEnabled=False)
    rules = raw.get('dateRules', [])
    if not isinstance(rules, list):
        raise ValueError('日期规则必须为列表')
    normalized = []
    identifiers = set()
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ValueError('日期规则必须为对象')
        try:
            start, end = date.fromisoformat(rule['startDate']), date.fromisoformat(rule['endDate'])
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError('日期规则须包含有效起止日期') from exc
        if start > end:
            raise ValueError('日期规则结束日期不能早于开始')
        ident = str(rule.get('id') or f'rule-{index}')
        if ident in identifiers:
            raise ValueError('日期规则标识不能重复')
        identifiers.add(ident)
        normalized.append({**validate_schedule(rule), 'id': ident, 'name': str(rule.get('name') or f'{start}～{end}'),
                           'startDate': start.isoformat(), 'endDate': end.isoformat()})
    normalized.sort(key=lambda rule: rule['startDate'])
    for previous, current in zip(normalized, normalized[1:]):
        if current['startDate'] <= previous['endDate']:
            raise ValueError(f'日期规则重叠：{previous["name"]} 与 {current["name"]}')
    result['dateRules'] = normalized
    return result


def schedule_for_day(settings: dict, work_date: date, override: dict | None) -> tuple[dict, str]:
    """按单日、日期段、默认的优先级确定完整作息。"""
    selected = settings
    label = '默认规则'
    for rule in settings['dateRules']:
        if rule['startDate'] <= work_date.isoformat() <= rule['endDate']:
            selected, label = rule, rule['name']
            break
    if override is not None:
        selected, label = validate_schedule(override), '单日覆盖'
    return {**validate_schedule(selected), 'standardMinutes': settings['standardMinutes']}, label
