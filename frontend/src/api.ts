export type User = { id: number; username: string; display_name: string; role: 'STUDENT' | 'TEACHER' }
export type Course = {
  id: number; code: string; name: string; description: string; capacity: number;
  enrollment_status: 'OPEN' | 'CLOSED'; enrolled_count: number; document_count: number;
  teachers: string; is_enrolled: boolean; can_manage: boolean;
}
export type CoursePayload = { code?: string; name: string; description: string; capacity: number; enrollment_status: 'OPEN' | 'CLOSED' }
export type CourseList = { items: Course[]; total: number; page: number; page_size: number }
export type Workspace = { course: Pick<Course, 'id' | 'code' | 'name' | 'description'>; question_answering_available: boolean }
export type Material = {
  id: number; course_id: number; owner_id: number; kind: 'TEACHING'|'NOTE'; title: string;
  original_filename: string; media_type: string; size_bytes: number; content_version: number;
  status: 'PROCESSING'|'PARSED'|'READY'|'FAILED'|'DELETED'; created_at: string; updated_at: string;
  course_name: string; can_manage: boolean; can_replace: boolean; index_status: string|null;
  can_retry:boolean; attempt_count:number; retry_round:number; chunk_count:number; processing_error:string|null;
}
export type ChunkList = {items:{chunk_index:number;content:string;page_number:number|null;section_title:string|null;char_start:number;char_end:number}[];total:number;page:number;page_size:number;content_version:number;searchable:boolean;chunking_profile:string}
export type MaterialList = { items: Material[]; total:number; page:number; page_size:number }

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message) }
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...options, credentials: 'same-origin',
    headers: { ...(options.body && !(options.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}), ...options.headers },
  })
  const data = await response.json().catch(() => ({ message: '服务响应异常，请稍后重试' }))
  if (!response.ok) throw new ApiError(response.status, data.code ?? 'REQUEST_FAILED', data.message ?? '请求失败')
  return data as T
}
