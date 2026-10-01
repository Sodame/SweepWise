from utils.language import language_prompt
from typing import Callable
from utils.prompt_loader import load_system_prompts, load_report_prompts
from langchain.agents import AgentState
from langchain.agents.middleware import wrap_tool_call, before_model, dynamic_prompt, ModelRequest
from langchain.tools.tool_node import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.runtime import Runtime
from langgraph.types import Command
from utils.logger_handler import logger
from agent.tools.tool_status import tool_status
from utils.report_dates import date_prompt


@wrap_tool_call
def monitor_tool(
        # 请求的数据封装
        request: ToolCallRequest,
        # 执行的函数本身
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
) -> ToolMessage | Command:             # 工具执行的监控
    if request.tool_call['name'] == 'fetch_external_data':
        resolved = request.runtime.context.get('report_month')
        if resolved and request.tool_call['args'].get('month') != resolved:
            logger.info(f"[report month]按本轮用户请求修正查询月份为 {resolved}")
            request = request.override(tool_call={**request.tool_call,
                'args': {**request.tool_call['args'], 'month': resolved}})
    logger.info(f"[tool monitor]执行工具：{request.tool_call['name']}")
    logger.info(f"[tool monitor]传入参数：{request.tool_call['args']}")

    try:
        result = handler(request)
        status = tool_status(result)
        if status == 'error':
            logger.warning(f"[tool monitor]工具{request.tool_call['name']}返回错误，交由模型处理")
            if isinstance(result, ToolMessage):
                result = result.model_copy(update={'status': 'error'})
        elif status == 'needs_input':
            logger.info(f"[tool monitor]工具{request.tool_call['name']}需要确认查询条件")
        else:
            logger.info(f"[tool monitor]工具{request.tool_call['name']}调用成功")

        if request.tool_call['name'] == "fill_context_for_report" and status == 'success':
            request.runtime.context["report"] = True

        return result
    except Exception as e:
        logger.error(f"工具{request.tool_call['name']}调用失败，原因：{str(e)}")
        raise e


@before_model
def log_before_model(
        state: AgentState,          # 整个Agent智能体中的状态记录
        runtime: Runtime,           # 记录了整个执行过程中的上下文信息
):         # 在模型执行前输出日志
    logger.info(f"[log_before_model]即将调用模型，带有{len(state['messages'])}条消息。")

    logger.debug(f"[log_before_model]{type(state['messages'][-1]).__name__}")

    return None


@dynamic_prompt                 # 每一次在生成提示词之前，调用此函数
def report_prompt_switch(request: ModelRequest):     # 动态切换提示词
    is_report = request.runtime.context.get("report", False)
    if is_report:               # 是报告生成场景，返回报告生成提示词内容
        return load_report_prompts() + date_prompt(request.runtime.context) + language_prompt(request.runtime.context)

    return load_system_prompts() + date_prompt(request.runtime.context) + language_prompt(request.runtime.context)
