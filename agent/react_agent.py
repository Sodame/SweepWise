from utils.language import translate
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, ToolMessage
from model.factory import chat_model
from utils.prompt_loader import load_system_prompts
from agent.tools.agent_tools import (rag_summarize, get_weather, get_user_location, get_user_id,
                                     get_current_month, fetch_external_data, fill_context_for_report)
from agent.tools.middleware import monitor_tool, log_before_model, report_prompt_switch
from agent.tools.tool_status import tool_status
from utils.report_dates import current_date, resolve_report_month


def text_content(content):
    if isinstance(content, str):
        return content
    return ''.join(block.get('text', '') for block in content if isinstance(block, dict) and block.get('type') == 'text')


class ReactAgent:
    def __init__(self):
        self.agent = create_agent(model=chat_model, system_prompt=load_system_prompts(),
            context_schema=dict,
            tools=[rag_summarize, get_weather, get_user_location, get_user_id,
                   get_current_month, fetch_external_data, fill_context_for_report],
            middleware=[monitor_tool, log_before_model, report_prompt_switch])

    def stream(self, messages, user):
        context = {'report': False, 'user_id': user['id'], 'username': user['username'], 'sources': [], 'warnings': []}
        context.update(report_user_id=user.get('report_user_id'), report_records_path=user.get('report_records_path'))
        context['language'] = 'en' if user.get('language') == 'en' else 'zh'
        context['browser_location'] = user.get('browser_location')
        today = current_date()
        latest_query = next((message.get('content', '') for message in reversed(messages)
                             if message.get('role') == 'user'), '')
        context['reference_date'] = today.isoformat()
        context['report_month'] = resolve_report_month(latest_query, today) if isinstance(latest_query, str) else None
        # A model may emit text first and append a tool call later in the same
        # response. Wait for the completed message before exposing its text.
        for payload in self.agent.stream({'messages': messages},
                stream_mode='updates', context=context, config={'recursion_limit': 32}):
            for update in payload.values():
                if not isinstance(update, dict):
                    continue
                for message in update.get('messages', []):
                    if isinstance(message, ToolMessage):
                        status = tool_status(message)
                        label = {'success': '工具执行完成', 'error': '查询失败，正在处理',
                                 'needs_input': '正在确认查询条件'}[status]
                        yield {'type': 'tool', 'name': message.name, 'message': translate(label, context['language'])}
                    elif isinstance(message, AIMessage):
                        for call in message.tool_calls:
                            yield {'type': 'tool', 'name': call['name'], 'message': translate('正在调用工具', context['language'])}
                        if not message.tool_calls and not message.invalid_tool_calls:
                            text = text_content(message.content)
                            if text:
                                yield {'type': 'token', 'content': text}
        for warning in context['warnings']:
            yield {'type': 'warning', 'message': translate(warning, context['language'])}
        if context['sources']:
            yield {'type': 'sources', 'sources': context['sources']}

    def execute_stream(self, query):
        for event in self.stream([{'role': 'user', 'content': query}], {'id': 'legacy', 'username': '访客'}):
            if event['type'] == 'token':
                yield event['content']
