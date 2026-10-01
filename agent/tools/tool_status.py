"""Classify framework errors and structured tool outcomes consistently."""
import json
from langchain_core.messages import ToolMessage


def tool_status(result):
    if not isinstance(result, ToolMessage):
        return 'success'
    if result.status == 'error':
        return 'error'
    if isinstance(result.content, str):
        try:
            content = json.loads(result.content)
        except ValueError:
            return 'success'
        if isinstance(content, dict):
            if content.get('error'):
                return 'error'
            if content.get('needs_clarification'):
                return 'needs_input'
    return 'success'
