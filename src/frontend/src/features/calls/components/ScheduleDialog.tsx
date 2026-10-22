import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Form as RACForm,
  Heading,
  Label,
  TextField,
} from 'react-aria-components'
import {
  RiCheckLine,
  RiDownloadLine,
  RiExternalLinkLine,
  RiFileCopyLine,
} from '@remixicon/react'
import { css } from '@/styled-system/css'
import {
  Button,
  Dialog,
  Field,
  Input,
  LinkButton,
  Text,
  useCloseDialog,
} from '@/primitives'
import { text } from '@/primitives/Text'
import { getRouteUrl } from '@/navigation/getRouteUrl'
import type { ApiRoom } from '@/features/rooms/api/ApiRoom'
import { setRoomCallTitle } from '../api/setRoomCallTitle'
import { useCreateCall } from '../hooks/useCreateCall'
import {
  buildIcs,
  googleCalendarUrl,
  icsFileName,
  type CalendarEvent,
} from '../utils/calendar'
import { formatDuration } from '../utils/dates'
import { downloadTextFile } from '../utils/download'
import { formatTimeZone, isStartInPast } from '../utils/schedule'

const APP_TITLE = import.meta.env.VITE_APP_TITLE || 'Fusion Meet'

const DURATIONS_MINUTES = [15, 30, 45, 60, 90, 120]
const DEFAULT_DURATION_MINUTES = 30
const COPIED_FEEDBACK_MS = 3000

const pad = (value: number) => String(value).padStart(2, '0')

// values of <input type="date"> and <input type="time">, in local time
const toDateInput = (date: Date) =>
  `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
const toTimeInput = (date: Date) =>
  `${pad(date.getHours())}:${pad(date.getMinutes())}`

// the next half hour, at least 15 minutes from now
const defaultStart = () => {
  const start = new Date(Date.now() + 15 * 60_000)
  start.setMinutes(start.getMinutes() < 30 ? 30 : 60, 0, 0)
  return start
}

const parseLocal = (date: string, time: string) => {
  const [year, month, day] = date.split('-').map(Number)
  const [hours, minutes] = time.split(':').map(Number)
  const result = new Date(year, month - 1, day, hours, minutes)
  return isNaN(result.getTime()) ? null : result
}

// like the labels of the Field primitive
const labelClass = css({ display: 'block', fontSize: '0.875rem' })

// the date and time inputs of browsers are taller than text ones
const inputClass = css({ height: '2.25rem' })

type ScheduledCall = {
  room: ApiRoom
  event: CalendarEvent
  isTitleSaved: boolean
}

const ScheduleForm = ({
  onScheduled,
}: {
  onScheduled: (call: ScheduledCall) => void
}) => {
  const { t, i18n } = useTranslation('calls', { keyPrefix: 'schedule' })
  const closeDialog = useCloseDialog()
  const { createRoomForLater } = useCreateCall()
  const [initialStart] = useState(defaultStart)
  const [title, setTitle] = useState('')
  const [date, setDate] = useState(() => toDateInput(initialStart))
  const [time, setTime] = useState(() => toTimeInput(initialStart))
  const [duration, setDuration] = useState(DEFAULT_DURATION_MINUTES)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [hasError, setHasError] = useState(false)
  const [isInPast, setIsInPast] = useState(false)
  // set at once: a double click or Enter pressed twice submits twice before
  // `isSubmitting` is rendered, and would create two rooms
  const isSubmittingRef = useRef(false)

  const defaultTitle = t('defaultTitle', { appTitle: APP_TITLE })
  // the inputs are in the browser's time zone, say which one
  const timeZone = formatTimeZone(parseLocal(date, time) ?? initialStart)

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (isSubmittingRef.current) return
    const start = parseLocal(date, time)
    if (!start) return
    if (isStartInPast(start)) {
      setIsInPast(true)
      return
    }
    isSubmittingRef.current = true
    setIsSubmitting(true)
    setIsInPast(false)
    setHasError(false)
    let room: ApiRoom
    try {
      room = await createRoomForLater()
    } catch {
      setHasError(true)
      setIsSubmitting(false)
      isSubmittingRef.current = false
      return
    }
    const callTitle = title.trim() || defaultTitle
    let isTitleSaved = true
    try {
      await setRoomCallTitle(room.id, callTitle)
    } catch {
      isTitleSaved = false
    }
    const url = getRouteUrl('room', room.slug)
    onScheduled({
      room,
      isTitleSaved,
      event: {
        uid: `${room.id}@${window.location.host}`,
        title: callTitle,
        description: t('description', { url }),
        url,
        start,
        end: new Date(start.getTime() + duration * 60_000),
      },
    })
  }

  const onDateOrTimeChange =
    (update: (value: string) => void) => (value: string) => {
      update(value)
      setIsInPast(false)
    }

  return (
    <RACForm onSubmit={onSubmit}>
      <TextField
        value={title}
        onChange={setTitle}
        maxLength={255}
        className={css({ marginBottom: '1rem' })}
      >
        <Label className={labelClass}>{t('name')}</Label>
        {/* eslint-disable-next-line jsx-a11y/no-autofocus -- the first field of the dialog */}
        <Input autoFocus placeholder={defaultTitle} className={inputClass} />
      </TextField>
      <div
        className={css({
          display: 'grid',
          gridTemplateColumns: { base: '1fr', xsm: '1fr 1fr' },
          gap: '1rem',
          marginBottom: '1rem',
        })}
      >
        <TextField
          value={date}
          onChange={onDateOrTimeChange(setDate)}
          isRequired
          isInvalid={isInPast}
        >
          <Label className={labelClass}>{t('date')}</Label>
          <Input
            type="date"
            min={toDateInput(new Date())}
            aria-describedby={isInPast ? 'schedule-past-error' : undefined}
            className={inputClass}
          />
        </TextField>
        <TextField
          value={time}
          onChange={onDateOrTimeChange(setTime)}
          isRequired
          isInvalid={isInPast}
        >
          <Label className={labelClass}>
            {t('timeWithZone', { zone: timeZone })}
          </Label>
          <Input
            type="time"
            aria-describedby={isInPast ? 'schedule-past-error' : undefined}
            className={inputClass}
          />
        </TextField>
      </div>
      <Field
        type="select"
        label={t('duration')}
        selectedKey={String(duration)}
        onSelectionChange={(key) => setDuration(Number(key))}
        items={DURATIONS_MINUTES.map((minutes) => ({
          value: String(minutes),
          label: formatDuration(minutes * 60_000, i18n.language),
        }))}
        wrapperProps={{ className: css({ marginBottom: '1.5rem' }) }}
      />
      {isInPast && (
        <Text
          id="schedule-past-error"
          variant="warning"
          role="alert"
          className={css({ marginBottom: '1rem' })}
        >
          {t('pastError')}
        </Text>
      )}
      {hasError && (
        <Text
          variant="warning"
          role="alert"
          className={css({ marginBottom: '1rem' })}
        >
          {t('error')}
        </Text>
      )}
      <div className={css({ display: 'flex', gap: '0.75rem' })}>
        <Button
          type="submit"
          variant="primary"
          loading={isSubmitting}
          isPending={isSubmitting}
        >
          {t('submit')}
        </Button>
        <Button variant="secondary" onPress={() => closeDialog?.()}>
          {t('cancel')}
        </Button>
      </div>
    </RACForm>
  )
}

const ScheduledCallView = ({ call }: { call: ScheduledCall }) => {
  const { t, i18n } = useTranslation('calls', { keyPrefix: 'schedule' })
  const closeDialog = useCloseDialog()
  const [isCopied, setIsCopied] = useState(false)
  const { event } = call

  useEffect(() => {
    if (!isCopied) return
    const timeout = setTimeout(() => setIsCopied(false), COPIED_FEEDBACK_MS)
    return () => clearTimeout(timeout)
  }, [isCopied])

  const when = useMemo(() => {
    const locale = i18n.language
    const day = new Intl.DateTimeFormat(locale, {
      weekday: 'long',
      day: 'numeric',
      month: 'long',
      ...(event.start.getFullYear() !== new Date().getFullYear() && {
        year: 'numeric',
      }),
    }).format(event.start)
    const hours = new Intl.DateTimeFormat(locale, { timeStyle: 'short' })
    return t('when', {
      day,
      start: hours.format(event.start),
      end: hours.format(event.end),
      zone: formatTimeZone(event.start),
    })
  }, [event, i18n.language, t])

  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(event.url)
      setIsCopied(true)
    } catch {
      // the link stays visible to copy by hand
    }
  }

  return (
    <>
      <Text variant="body" margin={false} bold>
        {event.title}
      </Text>
      <Text variant="note" margin={false}>
        {when}
      </Text>
      <p
        className={css({
          marginTop: '1rem',
          paddingX: '0.875rem',
          paddingY: '0.625rem',
          borderRadius: '10px',
          backgroundColor: 'surface.sunken',
          border: '1px solid {colors.border.subtle}',
          fontSize: '0.9375rem',
          overflowWrap: 'anywhere',
          userSelect: 'all',
        })}
      >
        {event.url.replace(/^https?:\/\//, '')}
      </p>
      {!call.isTitleSaved && (
        <Text variant="warning" className={css({ marginTop: '0.75rem' })}>
          {t('titleNotSaved')}
        </Text>
      )}
      <div
        className={css({
          display: 'flex',
          flexWrap: 'wrap',
          gap: '0.5rem',
          marginTop: '1rem',
        })}
      >
        <Button
          variant={isCopied ? 'success' : 'primary'}
          size="sm"
          onPress={copyLink}
        >
          {isCopied ? (
            <RiCheckLine size={18} aria-hidden="true" />
          ) : (
            <RiFileCopyLine size={18} aria-hidden="true" />
          )}
          {isCopied ? t('copied') : t('copy')}
        </Button>
        <Button
          variant="secondary"
          size="sm"
          onPress={() =>
            downloadTextFile(
              buildIcs(event, { productName: APP_TITLE }),
              icsFileName(event.title),
              'text/calendar;charset=utf-8'
            )
          }
        >
          <RiDownloadLine size={18} aria-hidden="true" />
          {t('ics')}
        </Button>
        <LinkButton
          variant="secondary"
          size="sm"
          href={googleCalendarUrl(event)}
          target="_blank"
          rel="noreferrer"
        >
          <RiExternalLinkLine size={18} aria-hidden="true" />
          {t('google')}
        </LinkButton>
      </div>
      <div
        className={css({
          display: 'flex',
          justifyContent: 'flex-end',
          marginTop: '1.5rem',
        })}
      >
        <Button variant="secondary" onPress={() => closeDialog?.()}>
          {t('done')}
        </Button>
      </div>
    </>
  )
}

const ScheduleDialogContent = () => {
  const { t } = useTranslation('calls', { keyPrefix: 'schedule' })
  const [scheduled, setScheduled] = useState<ScheduledCall | null>(null)
  return (
    <>
      <Heading slot="title" level={1} className={text({ variant: 'h1' })}>
        {scheduled ? t('doneHeading') : t('heading')}
      </Heading>
      {scheduled ? (
        <ScheduledCallView call={scheduled} />
      ) : (
        <ScheduleForm onScheduled={setScheduled} />
      )}
    </>
  )
}

/**
 * Schedule a call: a room titled after the meeting, then its link, an
 * .ics file and a Google Calendar link. Rendered in a DialogTrigger.
 */
export const ScheduleDialog = () => (
  <Dialog>
    <ScheduleDialogContent />
  </Dialog>
)
