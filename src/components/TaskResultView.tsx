import type { EmployeeTask } from '../api/tasks'

/** 작업 결과 표시는 직원 상세와 작업 기록에서 같은 규칙을 써야 하므로 한곳에 모은다. */
export function TaskResultView({ result }: { result: EmployeeTask['result'] }) {
  if (!result) return null

  const responseText = typeof result.response === 'string'
    ? result.response
    : typeof result.review === 'string' ? result.review : null
  const changedFiles = Array.isArray(result.changed_files)
    ? result.changed_files.filter((item): item is string => typeof item === 'string')
    : []
  const branch = typeof result.branch === 'string' ? result.branch : null
  const workspace = typeof result.workspace === 'string' ? result.workspace : null
  const diffStat = typeof result.diff_stat === 'string' ? result.diff_stat : null
  const skippedFiles = Array.isArray(result.secret_scan_skipped_files)
    ? result.secret_scan_skipped_files.filter((item): item is string => typeof item === 'string')
    : []
  // 검사하지 못한 파일도 확인 대상이라 sensitive_files 에 함께 들어 있다.
  // 사유가 다르므로 화면에서는 나눠 보여준다.
  const sensitiveFiles = (Array.isArray(result.sensitive_files)
    ? result.sensitive_files.filter((item): item is string => typeof item === 'string')
    : []
  ).filter((file) => !skippedFiles.includes(file))
  const noChanges = result.kind === 'no_changes'

  return (
    <>
      {responseText ? <p>{responseText}</p> : null}
      {noChanges ? (
        <p className="employee-task-result__empty">바뀐 파일이 없어 승인할 내용이 없습니다.</p>
      ) : null}
      {changedFiles.length > 0 ? (
        <>
          <h4>변경 파일</h4>
          <ul>{changedFiles.map((file) => <li key={file}>{file}</li>)}</ul>
        </>
      ) : null}
      {sensitiveFiles.length > 0 ? (
        <div className="employee-task-result__sensitive">
          <h4>별도 확인이 필요한 파일</h4>
          <ul>{sensitiveFiles.map((file) => <li key={file}>{file}</li>)}</ul>
          <p>검증 명령이나 실행 경로를 바꿀 수 있는 파일입니다. 승인 전에 내용을 직접 확인하세요.</p>
        </div>
      ) : null}
      {skippedFiles.length > 0 ? (
        <div className="employee-task-result__sensitive">
          <h4>비밀정보 검사를 못 한 파일</h4>
          <ul>{skippedFiles.map((file) => <li key={file}>{file}</li>)}</ul>
          <p>크기나 형식 때문에 내용을 확인하지 못했습니다. 검사하지 못한 것과 안전한 것은 다릅니다.</p>
        </div>
      ) : null}
      {branch ? <p><strong>검토 브랜치:</strong> {branch}</p> : null}
      {workspace ? <p><strong>분리 작업공간:</strong> {workspace}</p> : null}
      {diffStat ? <pre className="employee-task-result__diff">{diffStat}</pre> : null}
      {result.requires_approval === true ? (
        <p className="employee-task-result__approval">
          원본에는 아직 반영되지 않았습니다. 변경 내용을 확인한 뒤 승인해야 합니다.
        </p>
      ) : null}
    </>
  )
}
