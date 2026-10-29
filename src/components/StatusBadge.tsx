import { CircleDashed } from 'lucide-react'
import { getConnectionStatusLabel } from '../status'
import type { ConnectionStatus } from '../types'

export function StatusBadge({ status }: { status: ConnectionStatus }) {
  return (
    <span className={`status-badge status-badge--${status}`}>
      <CircleDashed aria-hidden="true" size={13} strokeWidth={2.4} />
      {getConnectionStatusLabel(status)}
    </span>
  )
}
