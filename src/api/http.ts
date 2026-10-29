export class ApiError extends Error {
  constructor(
    message: string,
    readonly code: string | null = null,
    readonly status: number | null = null,
  ) {
    super(message)
    this.name = 'ApiError'
  }

  /**
   * 네트워크 오류와 서버 오류(5xx)만 다시 시도한다.
   * 정책 거절이나 검증 실패(4xx)는 같은 요청을 반복해도 결과가 같다.
   */
  get retryable(): boolean {
    return this.status === null || this.status >= 500
  }
}

export interface RequestMessages {
  connectionFailed: string
  unreadable: string
  requestFailed: string
}

export async function requestJson(
  path: string,
  init: RequestInit | undefined,
  messages: RequestMessages,
  create: (message: string, code: string | null, status: number | null) => ApiError,
): Promise<unknown> {
  let response: Response
  try {
    response = await fetch(path, init)
  } catch {
    throw create(messages.connectionFailed, null, null)
  }
  let payload: unknown
  try {
    payload = await response.json()
  } catch {
    throw create(messages.unreadable, null, response.status)
  }
  if (!response.ok) {
    const apiError = payload as { error?: { code?: unknown; message?: unknown } }
    const message = typeof apiError?.error?.message === 'string'
      ? apiError.error.message
      : messages.requestFailed
    const code = typeof apiError?.error?.code === 'string' ? apiError.error.code : null
    throw create(message, code, response.status)
  }
  return payload
}

export const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)
export const isString = (value: unknown): value is string => typeof value === 'string'
export const isNullableString = (value: unknown): value is string | null =>
  value === null || isString(value)
export const stringList = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter(isString) : []
