"""Read the demo usage dataset without confusing account IDs with dataset IDs."""
import csv
import re
from pathlib import Path


def load_usage_records(path):
    records = {}
    fields = ('用户ID', '特征', '清洁效率', '耗材', '对比', '时间')
    with Path(path).open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        if not set(fields).issubset(reader.fieldnames or []):
            raise ValueError('使用记录 CSV 缺少必要表头：' + '、'.join(fields))
        for line, row in enumerate(reader, start=2):
            if None in row or any(row.get(field) is None for field in fields):
                raise ValueError(f'使用记录 CSV 第 {line} 行列数不正确')
            user_id, month = row['用户ID'].strip(), row['时间'].strip()
            if not user_id or not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', month):
                raise ValueError(f'使用记录 CSV 第 {line} 行的用户 ID 或月份不正确')
            months = records.setdefault(user_id, {})
            if month in months:
                raise ValueError(f'使用记录 CSV 存在重复记录：{user_id}/{month}')
            months[month] = {
                key: row[column].replace('\\n', '\n')
                for key, column in [('特征', '特征'), ('效率', '清洁效率'), ('耗材', '耗材'), ('对比', '对比')]
            }
    if not records:
        raise ValueError('使用记录 CSV 没有数据')
    return records
