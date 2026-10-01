"""Account-independent global weather, with per-request browser location."""
import json
import time
from typing import Annotated
from pydantic import BeforeValidator
from langchain.tools import ToolRuntime
from langchain_core.tools import tool
from utils.weather_client import WeatherError, current_weather, search_locations


def normalize_location_id(value):
    if isinstance(value, str) and value.strip().casefold() in {'', 'none', 'null'}:
        return None
    return value


def normalize_optional_text(value):
    return '' if value is None else value


LocationId = Annotated[int | None, BeforeValidator(normalize_location_id)]
OptionalText = Annotated[str, BeforeValidator(normalize_optional_text)]


def _json(data):
    return json.dumps(data, ensure_ascii=False)


def _coordinates(context):
    location = context.get('browser_location')
    if not location:
        raise WeatherError('location_required', '尚未共享当前位置，请点击输入框上方的“使用当前位置”并授权，或直接告诉我城市和国家。')
    age = time.time() - location['captured_at']
    if age > 300 or age < -60:
        raise WeatherError('location_expired', '当前位置已过期，请重新点击“使用当前位置”，或直接提供城市和国家。')
    return location['latitude'], location['longitude']


def _current(context, latitude, longitude):
    cache = context.setdefault('weather_cache', {})
    pair = (latitude, longitude)
    if pair not in cache:
        cache[pair] = current_weather(latitude, longitude)
    return cache[pair]


@tool(description='获取用户通过浏览器授权共享的当前位置对应的城市、地区和国家。无入参；未共享时提示点击“使用当前位置”。不会使用服务器IP定位。')
def get_user_location(runtime: ToolRuntime[dict]) -> str:
    try:
        latitude, longitude = _coordinates(runtime.context)
        data = _current(runtime.context, latitude, longitude)
        return _json({'source': data['source'], 'location': data['location'], 'position_source': '用户授权的浏览器位置；城市为天气服务匹配地点'})
    except WeatherError as exc:
        return _json({'error': str(exc), 'code': exc.code})


@tool(description='查询全球当前天气。city填城市名称（必要时使用英文），country可填英文国家全称；同名城市会返回候选列表，确认地点后用本轮返回的location_id再次调用。查询当前位置请传空对象{}。未使用的参数直接省略；location_id只能填写整数或JSON null，不要填写字符串None。')
def get_weather(runtime: ToolRuntime[dict], city: OptionalText = '', country: OptionalText = '', location_id: LocationId = None) -> str:
    context = runtime.context
    try:
        if location_id is not None:
            selected = context.get('weather_candidates', {}).get(location_id)
            if selected is None:
                raise WeatherError('invalid_location_id', '请先使用城市名称查询，再从本轮候选结果中选择 location_id，不得猜测 ID。')
            latitude, longitude = selected['lat'], selected['lon']
        elif city.strip():
            city = city.strip()
            if len(city) > 120 or ':' in city or not any(character.isalpha() for character in city):
                raise WeatherError('invalid_city', '请提供城市名称和国家；当前位置请使用浏览器定位按钮。')
            matches = search_locations(city)
            if not matches:
                raise WeatherError('city_not_found', '未找到对应城市，请补充国家或尝试英文城市名。')
            context.setdefault('weather_candidates', {}).update({item['id']: item for item in matches})
            filtered = [item for item in matches if str(item.get('country') or '').casefold() == country.strip().casefold()] if country.strip() else matches
            if len(filtered) != 1:
                return _json({'source': 'WeatherAPI.com', 'needs_clarification': True,
                    'message': '请根据用户已明确提供的国家/地区选择候选 ID；仍无法唯一确定时先询问用户，不得直接取第一项。',
                    'candidates': [{**item, 'location_id': item['id']} for item in (filtered or matches)]})
            selected = filtered[0]
            latitude, longitude = selected['lat'], selected['lon']
        else:
            if country.strip():
                raise WeatherError('city_required', '请同时提供城市名称；查询当前位置时城市和国家均留空。')
            latitude, longitude = _coordinates(context)
        return _json(_current(context, latitude, longitude))
    except WeatherError as exc:
        return _json({'error': str(exc), 'code': exc.code})
