"""Resolve a single explicit report month against a request's date snapshot."""
import re
from datetime import date, datetime

MONTHS = {name: index for index, name in enumerate(
    ('一', '二', '三', '四', '五', '六', '七', '八', '九', '十', '十一', '十二'), start=1)}
MONTH = r'(?:0?[1-9]|1[0-2]|十一|十二|十|[一二三四五六七八九])'
QUALIFIED = re.compile(rf'(?P<year>\d{{4}}\s*年|今年|去年|前年|明年|后年)\s*(?:的\s*)?(?P<month>{MONTH})\s*月')
ISO_MONTH = re.compile(r'(?<!\d)(\d{4})[-/](0?[1-9]|1[0-2])(?!\d)')
RELATIVE = {'上上个月': -2, '上上月': -2, '上个月': -1, '上月': -1,
            '本月': 0, '这个月': 0, '当月': 0, '下个月': 1, '下月': 1}
EN_NAMES = 'January February March April May June July August September October November December'.lower().split()
EN_MONTH = r'\b(?:' + '|'.join(name + '|' + name[:3] for name in EN_NAMES) + r')\b'
EN_YEAR = r'(?:\d{4}|(?:this|last|next)\s+year)'


def english_months(text, reference):
    text = text.lower()
    found, covered = [], []
    patterns = [rf'(?P<month>{EN_MONTH})\s+(?:of\s+)?(?P<year>{EN_YEAR})',
                rf'(?P<year>{EN_YEAR})[\s,]+(?P<month>{EN_MONTH})']
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            if any(start < match.end() and match.start() < end for start, end in covered):
                continue
            year_text = match['year']
            year = int(year_text) if year_text.isdigit() else reference.year + {'this': 0, 'last': -1, 'next': 1}[year_text.split()[0]]
            month = next(i for i, name in enumerate(EN_NAMES, 1) if name.startswith(match['month']))
            found.append(f'{year:04d}-{month:02d}')
            covered.append(match.span())
    if any(not any(start <= m.start() and m.end() <= end for start, end in covered)
           for m in re.finditer(EN_MONTH, text)):
        return None
    relative = {'two months ago': -2, 'month before last': -2, 'last month': -1, 'this month': 0, 'next month': 1}
    for match in re.finditer(r'\b(?:' + '|'.join(relative) + r')\b', text):
        year, month = divmod(reference.year * 12 + reference.month - 1 + relative[match[0]], 12)
        found.append(f'{year:04d}-{month + 1:02d}')
    return found


def current_date():
    return datetime.now().astimezone().date()


def resolve_report_month(text: str, reference: date):
    found = english_months(text, reference)
    if found is None:
        return None
    covered = []
    offsets = {'今年': 0, '去年': -1, '前年': -2, '明年': 1, '后年': 2}
    for match in QUALIFIED.finditer(text):
        year_text = match['year']
        year = reference.year + offsets[year_text] if year_text in offsets else int(year_text.rstrip('年').strip())
        month_text = match['month']
        month = MONTHS[month_text] if month_text in MONTHS else int(month_text)
        found.append(f'{year:04d}-{month:02d}')
        covered.append(match.span())
    for match in ISO_MONTH.finditer(text):
        found.append(f'{int(match[1]):04d}-{int(match[2]):02d}')
    for match in re.finditer('|'.join(RELATIVE), text):
        absolute = reference.year * 12 + reference.month - 1 + RELATIVE[match[0]]
        year, month = divmod(absolute, 12)
        found.append(f'{year:04d}-{month + 1:02d}')
    # Do not collapse a range or partly specified multi-month question into its
    # first month. The model receives today's date to interpret these in context.
    if re.search(r'月(?:份)?\s*(?:到|至|[-~～—])\s*[\d一二三四五六七八九十]', text):
        return None
    for match in re.finditer(rf'{MONTH}\s*月', text):
        if not any(start <= match.start() and match.end() <= end for start, end in covered):
            return None
    return found[0] if len(found) == 1 else None


def date_prompt(context):
    reference = context.get('reference_date') or current_date().isoformat()
    year = date.fromisoformat(reference).year
    if context.get('language') == 'en':
        prompt = f'\nCurrent date (backend local timezone): {reference}. This year is {year}; last year is {year - 1}. Resolve relative dates against this date, never training data or historical records.'
        if context.get('report_month'):
            prompt += f"\nThe requested report month is {context['report_month']}. Use this month for the query and report title; respect the month returned by the tool."
        return prompt
    prompt = (f'\n当前日期（后端本地时区）：{reference}。相对日期必须以此为准：'
              f'今年是{year}年，去年是{year - 1}年，前年是{year - 2}年。'
              '不得根据训练知识、历史对话日期或模拟数据年份推测今天。')
    if context.get('report_month'):
        prompt += f"\n本轮用户明确指定的报告月份已由程序解析为 {context['report_month']}；查询及报告标题必须使用此月份，以工具返回的 month 为准。"
    return prompt
