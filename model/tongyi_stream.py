"""DashScope incremental tool streams, including sparse parallel call indices."""
from copy import deepcopy

from langchain_community.chat_models.tongyi import ChatTongyi
from langchain_core.messages import AIMessageChunk


class StreamingChatTongyi(ChatTongyi):
    def _invocation_params(self, messages, stop, **kwargs):
        params = super()._invocation_params(messages, stop, **kwargs)
        if params.get('stream'):
            # Current DashScope supports incremental tool arguments. The community
            # adapter's cumulative subtraction assumes every frame has a name.
            params['incremental_output'] = True
        return params

    @staticmethod
    def _chat_generation_from_qwen_resp(resp, is_chunk=False, is_last_chunk=True):
        resp = deepcopy(resp)
        raw = resp['output']['choices'][0]['message']
        content = raw.get('content')
        if isinstance(content, list):
            raw['content'] = ''.join(
                block.get('text', '') for block in content if isinstance(block, dict)
            )
        elif content is None:
            raw['content'] = ''
        result = ChatTongyi._chat_generation_from_qwen_resp(resp, is_chunk, is_last_chunk)
        if is_chunk and raw.get('tool_calls'):
            # A frame may contain only call index 1. Enumerating that frame from
            # zero would incorrectly merge its arguments into the first call.
            message = result['message']
            chunks = [
                {**chunk, 'index': call.get('index', position)}
                for position, (chunk, call) in enumerate(zip(message.tool_call_chunks, raw['tool_calls']))
            ]
            result['message'] = AIMessageChunk(
                **message.model_dump(exclude={'tool_call_chunks', 'tool_calls', 'invalid_tool_calls'}),
                tool_call_chunks=chunks,
            )
        return result
