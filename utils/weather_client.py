"""WeatherAPI requests with bounded timeouts and credential-safe errors."""
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen


class WeatherError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


ERRORS = {
    1002: '后端未配置 WEATHERAPI_API_KEY。',
    1006: '未找到对应城市，请补充国家或尝试英文城市名。',
    2006: 'WeatherAPI Key 无效，请检查后端配置。',
    2007: 'WeatherAPI 本月免费调用额度已用完。',
    2008: 'WeatherAPI Key 已被停用。',
    2009: '当前 WeatherAPI 套餐无权访问此接口。',
}


def _provider_error(body, fallback='provider_error'):
    error = body.get('error', {}) if isinstance(body, dict) else {}
    code = error.get('code', fallback) if isinstance(error, dict) else fallback
    if not isinstance(code, int):
        code = fallback
    # Do not include raw provider messages/URLs: they may contain credentials.
    return WeatherError(code, ERRORS.get(code, '天气服务暂时不可用，请稍后重试。'))


def weather_request(endpoint, query):
    key = (os.getenv('WEATHERAPI_API_KEY') or '').strip()
    if not key:
        raise WeatherError('missing_key', ERRORS[1002])
    url = 'https://api.weatherapi.com/v1/' + endpoint + '?' + urlencode({'key': key, 'q': query, 'lang': 'zh'})
    try:
        with urlopen(url, timeout=10) as response:
            body = json.load(response)
    except HTTPError as exc:
        try:
            with exc:
                body = json.load(exc)
        except (ValueError, OSError):
            body = {}
        raise _provider_error(body, exc.code) from None
    except (TimeoutError, URLError, OSError):
        raise WeatherError('network_error', '无法连接 WeatherAPI 或请求超时，请检查网络后重试。') from None
    except ValueError:
        raise WeatherError('invalid_response', '天气服务返回了无法解析的数据。') from None
    if isinstance(body, dict) and 'error' in body:
        raise _provider_error(body)
    return body


def search_locations(city):
    result = weather_request('search.json', city)
    if not isinstance(result, list):
        raise WeatherError('invalid_response', '城市搜索服务返回格式异常。')
    return [{key: item.get(key) for key in ('id', 'name', 'region', 'country', 'lat', 'lon')}
            for item in result if isinstance(item, dict) and isinstance(item.get('id'), int)
            and isinstance(item.get('lat'), (int, float)) and isinstance(item.get('lon'), (int, float))]


def current_weather(latitude, longitude):
    result = weather_request('current.json', f'{latitude},{longitude}')
    if not isinstance(result, dict) or not isinstance(result.get('location'), dict) or not isinstance(result.get('current'), dict):
        raise WeatherError('invalid_response', '天气服务返回的数据缺少城市或天气信息。')
    location, current = result['location'], result['current']
    condition = current.get('condition') if isinstance(current.get('condition'), dict) else {}
    return {
        'source': 'WeatherAPI.com',
        'location': {key: location.get(key) for key in ('name', 'region', 'country', 'lat', 'lon', 'tz_id', 'localtime')},
        'weather': {
            'condition': condition.get('text'), 'temperature_c': current.get('temp_c'),
            'feelslike_c': current.get('feelslike_c'), 'humidity_percent': current.get('humidity'),
            'wind_kph': current.get('wind_kph'), 'wind_direction': current.get('wind_dir'),
            'precipitation_mm': current.get('precip_mm'), 'last_updated': current.get('last_updated'),
        },
    }
