import { requestLanguage, translate } from './i18n'
export type User = {id: string; username: string}
export type BrowserLocation = {latitude: number; longitude: number; captured_at: number}
export type Source = {title: string; page?: number; preview: string; rerank_score?: number}
export type Message = {id: string; role: 'user' | 'assistant'; content: string; status: string; sources: Source[]}
export type Conversation = {id: string; title: string; updated: number}
export type StreamEvent = {type: string; content?: string; message?: string; name?: string; sources?: Source[]}

export async function checkResponse(response: Response) {
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    if (response.status === 401) window.dispatchEvent(new Event('auth-expired'))
    throw new Error(typeof body.detail === 'string' ? body.detail : translate('请求失败，请检查输入后重试'))
  }
  return response
}

export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await checkResponse(await fetch('/api' + path, {
    method, credentials: 'include', headers: {'Content-Type': 'application/json', 'Accept-Language': requestLanguage()},
    ...(body === undefined ? {} : {body: JSON.stringify(body)}),
  }))
  return response.status === 204 ? undefined as T : response.json()
}

export async function streamAnswer(id: string, content: string, signal: AbortSignal, onEvent: (event: StreamEvent) => void, location?: BrowserLocation) {
  const response = await checkResponse(await fetch(`/api/conversations/${id}/messages`, {
    method: 'POST', credentials: 'include', headers: {'Content-Type': 'application/json', 'Accept-Language': requestLanguage()},
    body: JSON.stringify({content, language: requestLanguage(), ...(location ? {location} : {})}), signal,
  }))
  if (!response.body) throw new Error(translate('浏览器不支持流式响应'))
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = '', completed = false
  try {
    while (true) {
      const {value, done} = await reader.read()
      buffer += decoder.decode(value, {stream: !done})
      buffer = buffer.replace(/\r\n/g, '\n')
      let boundary: number
      while ((boundary = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        const data = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n')
        if (!data) continue
        const event = JSON.parse(data) as StreamEvent
        if (event.type === 'error') throw new Error(event.message || translate('回答生成失败'))
        if (event.type === 'done') completed = true
        onEvent(event)
      }
      if (done) break
    }
    if (!completed) throw new Error(translate('连接已中断，请稍后重试'))
  } finally {
    await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}
