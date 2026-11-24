export type User = { id: number; username: string; display_name: string; role: 'STUDENT' | 'TEACHER' }
export type Course = {
  id: number; code: string; name: string; description: string; capacity: number;
  enrollment_status: 'OPEN' | 'CLOSED'; enrolled_count: number; document_count: number;
  teachers: string; is_enrolled: boolean; can_manage: boolean;
}
export type CoursePayload = { code?: string; name: string; description: string; capacity: number; enrollment_status: 'OPEN' | 'CLOSED' }
export type CourseList = { items: Course[]; total: number; page: number; page_size: number }
export type Workspace = { course: Pick<Course, 'id' | 'code' | 'name' | 'description'>; documents: {id:number;title:string;kind:string;status:string}[]; question_answering_available: boolean }

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message) }
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...options, credentials: 'same-origin',
    headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers },
  })
  const data = await response.json().catch(() => ({ message: '服务响应异常，请稍后重试' }))
  if (!response.ok) throw new ApiError(response.status, data.code ?? 'REQUEST_FAILED', data.message ?? '请求失败')
  return data as T
}
