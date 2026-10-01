from agent.tools.weather_tools import get_weather, get_user_location
from utils.logger_handler import logger
from langchain_core.tools import tool
from rag.rag_service import RagSummarizeService
from utils.report_dates import current_date
from langchain.tools import ToolRuntime
from utils.config_handler import agent_conf
from utils.path_tool import get_abs_path
from utils.usage_records import load_usage_records
import json
import threading

_rag = {}
_rag_lock = threading.Lock()


def get_rag(language='zh'):
    if language not in {'zh', 'en'}:
        raise ValueError('Unsupported knowledge language')
    with _rag_lock:
        if language not in _rag:
            _rag[language] = RagSummarizeService(language)
        return _rag[language]


@tool(description="从向量存储中检索参考资料")
def rag_summarize(query: str, runtime: ToolRuntime[dict]) -> str:
    language = runtime.context.get('language', 'zh')
    rag = get_rag(language)
    docs, warning = rag.retrieve(query)
    runtime.context['sources'].extend(rag.sources(docs))
    if warning:
        runtime.context['warnings'].append(warning)
    if not docs:
        return ('No matching information in the English knowledge base. Tell the user honestly; do not invent an answer.'
                if language == 'en' else '知识库未找到相关资料，请如实告知用户，不要编造。')
    return rag.context(docs, language=language)


@tool(description="获取用户的ID，以纯字符串形式返回")
def get_user_id(runtime: ToolRuntime[dict]) -> str:
    return runtime.context['user_id']


@tool(description="获取当前月份，以纯字符串形式返回")
def get_current_month(runtime: ToolRuntime[dict]) -> str:
    return (runtime.context.get('reference_date') or current_date().isoformat())[:7]


@tool(description="读取当前登录账号绑定的模拟使用记录，无需用户ID。month为YYYY-MM；用户未指定月份时留空，自动选用数据中最新月份。返回模拟ID、实际月份、可用月份及记录或错误信息。")
def fetch_external_data(runtime: ToolRuntime[dict], month: str = '') -> str:
    demo_id = runtime.context.get('report_user_id')
    result = {'data_source': 'records.csv 模拟使用数据', 'is_demo': True, 'demo_user_id': demo_id}
    if not demo_id:
        result['error'] = '当前账号尚未分配模拟使用记录；模拟用户按注册顺序一对一分配，数据名额可能已用完或数据文件不可用。'
        return json.dumps(result, ensure_ascii=False)
    path = runtime.context.get('report_records_path') or get_abs_path(agent_conf['external_data_path'])
    try:
        records = load_usage_records(path)
    except (OSError, ValueError):
        logger.exception('[fetch_external_data]模拟使用记录读取失败')
        result['error'] = '模拟使用记录文件无法读取，请检查 CSV 文件及表头。'
        return json.dumps(result, ensure_ascii=False)
    months = records.get(demo_id, {})
    result['available_months'] = sorted(months)
    selected = runtime.context.get('report_month') or month.strip() or max(months, default='')
    result['month'] = selected
    if runtime.context.get('reference_date'):
        result['reference_date'] = runtime.context['reference_date']
    if selected not in months:
        result['error'] = '指定月份暂无记录，请从 available_months 中选择。' if months else '当前账号绑定的模拟用户在 CSV 中已无记录。'
    else:
        result['records'] = months[selected]
    return json.dumps(result, ensure_ascii=False)


@tool(description="无入参，无返回值，调用后触发中间件自动为报告生成的场景动态注入上下文信息，为后续提示词切换提供上下文信息")
def fill_context_for_report():
    return "fill_context_for_report已调用"


# if __name__ == '__main__':
#     print(get_weather(get_user_location()))
