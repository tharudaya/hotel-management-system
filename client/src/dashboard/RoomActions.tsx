import { useId, useState } from 'react'

import { ApiError } from '../api/client'
import type { DashboardRoom, HousekeepingStatus } from '../api/types'
import { useAuthenticatedUser } from '../auth/currentUser'
import { buttonPrimary, buttonSecondary } from '../components/styles'
import { canChangeHousekeeping, HOUSEKEEPING_TRANSITIONS } from './housekeeping'
import { type RoomAction, useRoomAction } from './queries'
import { HOUSEKEEPING } from './status'

const READY_FOR_GUEST: HousekeepingStatus[] = ['clean', 'inspected']

type Feedback = { kind: 'success' | 'error'; text: string } | null

/** Check-in / check-out / housekeeping buttons for one room. Remount per room (key). */
export function RoomActions({ room }: { room: DashboardRoom }) {
  const user = useAuthenticatedUser()
  const action = useRoomAction()
  const [feedback, setFeedback] = useState<Feedback>(null)
  const hintId = useId()

  function run(request: RoomAction, successText: string) {
    setFeedback(null)
    action.mutate(request, {
      onSuccess: () => {
        setFeedback({ kind: 'success', text: successText })
      },
      onError: (error) => {
        setFeedback({ kind: 'error', text: describeError(error, room.number) })
      },
    })
  }

  const pending = action.isPending ? action.variables : undefined
  const reservationId = room.reservation_id
  const canCheckIn = room.occupancy_status === 'arriving_today' && reservationId !== null
  const ready = READY_FOR_GUEST.includes(room.housekeeping_status)
  const canCheckOut =
    (room.occupancy_status === 'occupied' || room.occupancy_status === 'departing_today') &&
    reservationId !== null

  return (
    <div className="space-y-4">
      {(canCheckIn || canCheckOut) && (
        <div className="space-y-2">
          {canCheckIn && (
            <>
              <button
                type="button"
                disabled={action.isPending || !ready}
                aria-describedby={ready ? undefined : hintId}
                onClick={() => {
                  run(
                    {
                      kind: 'check-in',
                      reservationId,
                      roomId: room.room_id,
                      roomVersion: room.version,
                    },
                    `${room.guest_name ?? 'Guest'} checked in to room ${room.number}.`,
                  )
                }}
                className={`${buttonPrimary} w-full`}
              >
                {pending?.kind === 'check-in' ? 'Checking in…' : 'Check in'}
              </button>
              {!ready && (
                <p id={hintId} className="text-sm text-slate-600">
                  The room must be clean or inspected before check-in.
                </p>
              )}
            </>
          )}
          {canCheckOut && (
            <button
              type="button"
              disabled={action.isPending}
              onClick={() => {
                run(
                  { kind: 'check-out', reservationId, roomVersion: room.version },
                  `${room.guest_name ?? 'Guest'} checked out of room ${room.number}.`,
                )
              }}
              className={`${buttonPrimary} w-full`}
            >
              {pending?.kind === 'check-out'
                ? 'Checking out…'
                : room.occupancy_status === 'occupied'
                  ? 'Check out early'
                  : 'Check out'}
            </button>
          )}
        </div>
      )}

      {canChangeHousekeeping(user.role) && (
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium text-slate-700">Housekeeping</legend>
          <div className="flex flex-wrap gap-2">
            {HOUSEKEEPING_TRANSITIONS[room.housekeeping_status].map((status) => (
              <button
                key={status}
                type="button"
                disabled={action.isPending}
                onClick={() => {
                  run(
                    {
                      kind: 'housekeeping',
                      roomId: room.room_id,
                      status,
                      version: room.version,
                    },
                    `Room ${room.number} marked ${HOUSEKEEPING[status].label.toLowerCase()}.`,
                  )
                }}
                className={buttonSecondary}
              >
                {pending?.kind === 'housekeeping' && pending.status === status
                  ? 'Saving…'
                  : `Mark ${HOUSEKEEPING[status].label.toLowerCase()}`}
              </button>
            ))}
          </div>
        </fieldset>
      )}

      {/* Always mounted so screen readers announce the outcome. */}
      <div role="status" className={feedback?.kind === 'success' ? successBox : 'sr-only'}>
        {feedback?.kind === 'success' ? feedback.text : ''}
      </div>
      <div role="alert" className={feedback?.kind === 'error' ? errorBox : 'sr-only'}>
        {feedback?.kind === 'error' ? feedback.text : ''}
      </div>
    </div>
  )
}

const successBox =
  'rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-900'
const errorBox = 'rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800'

function describeError(error: Error, roomNumber: string): string {
  if (error instanceof ApiError) {
    if (error.status === 409 && error.code === 'version_conflict') {
      return `Room ${roomNumber} was changed by someone else. The latest details are now shown; please check them and try again.`
    }
    // Other conflicts are business rules with a message written for staff
    // (e.g. "Room 103 is dirty; it must be clean or inspected.").
    if (error.status === 409) return error.message
    if (error.status === 404) {
      return 'This room or reservation no longer exists. The dashboard has been refreshed.'
    }
    if (error.status === 403) return 'You do not have permission to do that.'
  }
  return 'The action failed. Please try again.'
}
