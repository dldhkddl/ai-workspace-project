import { useEffect, useState } from 'react'
import {
  dispatchProjectPlan, getProjectWorkflow, TaskApiError,
  respondToProjectPlan,
  type ProjectWorkflow, type TaskStatus,
} from '../api/tasks'

interface Assignment {
  employee_id: string
  task: string
  expected_output: string
}

interface Plan {
  objective: string
  completion_criteria: string[]
  assignments: Assignment[]
  risks: string[]
  needs_user_choice: boolean
  user_question: string | null
  user_choices: string[]
}

function readLegacyChoices(question: string | null): { question: string | null, choices: string[] } {
  if (!question) return { question, choices: [] }
  const marker = /(?:^|\s)([①②③④⑤⑥⑦⑧⑨⑩]|\d+[.)])\s*/g
  const matches = [...question.matchAll(marker)]
  if (matches.length < 2) return { question, choices: [] }
  const choices = matches.map((match, index) => {
    const start = (match.index ?? 0) + match[0].length
    const end = index + 1 < matches.length ? matches[index + 1].index : question.length
    return question.slice(start, end).trim()
  }).filter(Boolean)
  if (choices.length < 2) return { question, choices: [] }
  return { question: question.slice(0, matches[0].index).trim(), choices }
}

const statusLabels: Record<TaskStatus, string> = {
  queued: '대기', running: '진행 중', cancel_requested: '취소 요청',
  cancelled: '취소됨', completed: '완료', failed: '실패',
}

function readPlan(result: Record<string, unknown>): Plan | null {
  const value = result.plan
  if (!value || typeof value !== 'object') return null
  const plan = value as Partial<Plan>
  if (typeof plan.objective !== 'string' || !Array.isArray(plan.assignments)) return null
  const rawQuestion = typeof plan.user_question === 'string' ? plan.user_question : null
  const structuredChoices = Array.isArray(plan.user_choices)
    ? plan.user_choices.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
    : []
  const legacy = structuredChoices.length > 0
    ? { question: rawQuestion, choices: structuredChoices }
    : readLegacyChoices(rawQuestion)
  return {
    objective: plan.objective,
    completion_criteria: Array.isArray(plan.completion_criteria) ? plan.completion_criteria : [],
    assignments: plan.assignments,
    risks: Array.isArray(plan.risks) ? plan.risks : [],
    needs_user_choice: plan.needs_user_choice === true,
    user_question: legacy.question,
    user_choices: legacy.choices,
  }
}

export function ProjectWorkflowPanel({ taskId, result, employeeNames, onResponseSubmitted }: {
  taskId: string
  result: Record<string, unknown>
  employeeNames?: ReadonlyMap<string, string>
  onResponseSubmitted?: (taskId: string) => void
}) {
  const employeeName = (employeeId: string) => employeeNames?.get(employeeId) ?? employeeId
  const plan = readPlan(result)
  const [workflow, setWorkflow] = useState<ProjectWorkflow | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [showRevision, setShowRevision] = useState(false)
  const [revisionMessage, setRevisionMessage] = useState('')

  useEffect(() => {
    if (!plan) return
    let active = true
    const refresh = async () => {
      try {
        const current = await getProjectWorkflow(taskId)
        if (!active) return
        setWorkflow(current)
        setError(null)
      } catch (caught) {
        if (active) setError(caught instanceof TaskApiError ? caught.message : 'PM 작업 상태를 확인하지 못했습니다.')
      }
    }
    void refresh()
    return () => {
      active = false
    }
  }, [taskId, plan?.objective])

  useEffect(() => {
    if (!workflow || !['replanning', 'running', 'discussing', 'synthesizing'].includes(workflow.status)) return
    let active = true
    let timer: number | undefined
    const poll = async () => {
      try {
        const current = await getProjectWorkflow(taskId)
        if (!active) return
        setWorkflow(current)
        setError(null)
        if (['replanning', 'running', 'discussing', 'synthesizing'].includes(current.status)) {
          timer = window.setTimeout(() => void poll(), 3000)
        }
      } catch (caught) {
        if (active) setError(caught instanceof TaskApiError ? caught.message : 'PM 작업 상태를 확인하지 못했습니다.')
      }
    }
    timer = window.setTimeout(() => void poll(), 3000)
    return () => {
      active = false
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [taskId, workflow?.status])

  if (!plan) return null

  const retryStatus = async () => {
    try {
      setWorkflow(await getProjectWorkflow(taskId))
      setError(null)
    } catch (caught) {
      setError(caught instanceof TaskApiError ? caught.message : 'PM 작업 상태를 확인하지 못했습니다.')
    }
  }

  const execute = async () => {
    if (submitting || plan.needs_user_choice) return
    setSubmitting(true)
    setError(null)
    try {
      setWorkflow(await dispatchProjectPlan(taskId))
    } catch (caught) {
      setError(caught instanceof TaskApiError ? caught.message : '계획을 배분하지 못했습니다.')
    } finally {
      setSubmitting(false)
    }
  }

  const respond = async (action: 'approve' | 'revise', selectedMessage?: string) => {
    const message = action === 'revise' ? (selectedMessage ?? revisionMessage).trim() : null
    if (submitting || (action === 'revise' && !message)) return
    setSubmitting(true)
    setError(null)
    try {
      const continuation = await respondToProjectPlan(taskId, { action, message })
      setWorkflow((current) => ({
        status: 'replanning',
        plan_task_id: taskId,
        assignments: current?.assignments ?? [],
        synthesis: current?.synthesis ?? null,
        continuation,
      }))
      setShowRevision(false)
      onResponseSubmitted?.(taskId)
    } catch (caught) {
      setError(caught instanceof TaskApiError ? caught.message : 'PM에게 답변을 보내지 못했습니다.')
    } finally {
      setSubmitting(false)
    }
  }

  const report = workflow?.synthesis?.result?.report
  const reportObject = report && typeof report === 'object' ? report as Record<string, unknown> : null
  const renderList = (value: unknown) => Array.isArray(value)
    ? <ul>{value.filter((item): item is string => typeof item === 'string').map((item) => <li key={item}>{item}</li>)}</ul>
    : null

  return (
    <section className="project-workflow" aria-label="PM 작업 계획">
      <h3>PM 실행 계획</h3>
      <p>{plan.objective}</p>
      {plan.completion_criteria.length > 0 ? <><h4>완료 기준</h4><ul>{plan.completion_criteria.map((item) => <li key={item}>{item}</li>)}</ul></> : null}
      <h4>담당 업무</h4>
      <ul>{plan.assignments.map((item) => (
        <li key={item.employee_id}><strong>{employeeName(item.employee_id)}</strong> — {item.task} ({item.expected_output})</li>
      ))}</ul>
      {plan.user_question ? <p className="project-workflow__question">PM 확인 필요: {plan.user_question}</p> : null}
      {plan.needs_user_choice && (!workflow?.continuation) ? (
        <div className="project-workflow__response">
          {plan.user_choices.length > 0 ? (
            <div className="project-workflow__choices" aria-label="PM 선택지">
              {plan.user_choices.map((choice, index) => (
                <button key={choice} type="button" disabled={submitting} onClick={() => void respond('revise', choice)}>
                  {index + 1}. {choice}
                </button>
              ))}
              <button type="button" disabled={submitting} onClick={() => setShowRevision(true)}>직접 입력</button>
            </div>
          ) : (
            <div className="project-workflow__response-actions">
              <button type="button" disabled={submitting} onClick={() => void respond('approve')}>
                {submitting ? '보내는 중…' : 'PM 제안대로 진행'}
              </button>
              <button type="button" disabled={submitting} onClick={() => setShowRevision(true)}>수정 요청</button>
            </div>
          )}
          {showRevision ? (
            <div className="project-workflow__revision">
              <label htmlFor={`pm-response-${taskId}`}>PM에게 보낼 답변</label>
              <textarea
                id={`pm-response-${taskId}`}
                value={revisionMessage}
                onChange={(event) => setRevisionMessage(event.target.value)}
                rows={4}
                placeholder="바꿀 범위나 우선순위를 적어 주세요."
              />
              <div className="project-workflow__response-actions">
                <button type="button" disabled={submitting || !revisionMessage.trim()} onClick={() => void respond('revise')}>답변 보내기</button>
                <button type="button" disabled={submitting} onClick={() => setShowRevision(false)}>답변 취소</button>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
      {workflow?.status === 'replanning' ? <p className="project-workflow__notice">PM이 답변을 반영해 계획을 다시 세우고 있습니다.</p> : null}
      {workflow?.status === 'not_started' || workflow === null ? (
        <button type="button" disabled={submitting || plan.needs_user_choice || workflow === null} onClick={() => void execute()}>
          {submitting ? '배분 중…' : '계획 실행'}
        </button>
      ) : null}
      {workflow?.status === 'running' ? <h4>직원 작업 진행 중</h4> : null}
      {workflow?.status === 'discussing' ? <h4>의견 충돌을 재검토하고 반박하는 중</h4> : null}
      {workflow?.status === 'synthesizing' ? <h4>PM이 결과를 종합하고 있습니다</h4> : null}
      {workflow && workflow.assignments.length > 0 ? (
        <ul className="project-workflow__progress">{workflow.assignments.map((item) => (
          <li key={item.id}><span>{employeeName(item.employee_id)}</span><strong>{statusLabels[item.status]}</strong></li>
        ))}</ul>
      ) : null}
      {workflow?.discussions && workflow.discussions.length > 0 ? (
        <>
          <h4>의견 충돌 재검토</h4>
          <ul className="project-workflow__progress">{workflow.discussions.map((item) => (
            <li key={item.id}><span>{employeeName(item.employee_id)}</span><strong>{statusLabels[item.status]}</strong></li>
          ))}</ul>
        </>
      ) : null}
      {reportObject ? (
        <section className="project-workflow__report">
          <h4>PM 종합 보고</h4>
          {typeof reportObject.summary === 'string' ? <p>{reportObject.summary}</p> : null}
          {renderList(reportObject.completed_items)}
          {renderList(reportObject.incomplete_items)}
          {renderList(reportObject.risks)}
          {renderList(reportObject.next_actions)}
        </section>
      ) : null}
      {workflow?.status === 'failed' && workflow.synthesis?.error ? (
        <p role="alert">PM 종합 실패: {workflow.synthesis.error.message}</p>
      ) : null}
      {workflow?.continuation?.status === 'failed' && workflow.continuation.error ? (
        <p role="alert">PM 후속 계획 실패: {workflow.continuation.error.message}</p>
      ) : null}
      {workflow?.continuation?.status === 'completed' && workflow.continuation.result ? (
        <div className="project-workflow__continuation">
          <ProjectWorkflowPanel
            taskId={workflow.continuation.id}
            result={workflow.continuation.result}
            employeeNames={employeeNames}
          />
        </div>
      ) : null}
      {error ? (
        <div>
          <p role="alert">{error}</p>
          <button type="button" onClick={() => void retryStatus()}>다시 확인</button>
        </div>
      ) : null}
    </section>
  )
}
