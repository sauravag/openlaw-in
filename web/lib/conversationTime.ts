import { getStrings } from '@/lib/strings'

const LOCALE_BY_LANGUAGE: Readonly<Record<string, string>> = { hi: 'hi-IN' }
const DEFAULT_LOCALE = 'en-IN'

function localeFor(language: string): string {
  return LOCALE_BY_LANGUAGE[language] ?? DEFAULT_LOCALE
}

/** True when both dates fall on the same local calendar day. */
function isSameLocalDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  )
}

function dayBefore(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate() - 1)
}

/**
 * A sidebar entry's time: "Today, 9:03", "Yesterday, 18:40", "12 Nov", or
 * "12 Nov 2025" for another year. 24-hour clock, the reader's local time.
 * A malformed timestamp is returned as given rather than thrown on.
 */
export function formatConversationTime(iso: string, language: string, now: Date): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  const locale = localeFor(language)
  const strings = getStrings(language)
  // Intl pads the hour ("09:03") under h23; the copy reads "9:03".
  const time = (): string =>
    new Intl.DateTimeFormat(locale, {
      hour: 'numeric',
      minute: '2-digit',
      hourCycle: 'h23',
    })
      .format(date)
      .replace(/^0(\d)/, '$1')

  if (isSameLocalDay(date, now)) return strings.todayAt(time())
  if (isSameLocalDay(date, dayBefore(now))) return strings.yesterdayAt(time())
  return new Intl.DateTimeFormat(locale, {
    day: 'numeric',
    month: 'short',
    ...(date.getFullYear() === now.getFullYear() ? {} : { year: 'numeric' as const }),
  }).format(date)
}
