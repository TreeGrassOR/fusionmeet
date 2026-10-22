import { useLayoutEffect, useMemo, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Button as RACButton,
  GridList,
  GridListLoadMoreItem,
  Input,
  SearchField,
} from 'react-aria-components'
import { useSnapshot } from 'valtio'
import { RiAddLine, RiCloseLine, RiSearchLine } from '@remixicon/react'
import { css } from '@/styled-system/css'
import { Button, Text } from '@/primitives'
import { Spinner } from '@/primitives/Spinner'
import { navigateTo } from '@/navigation/navigateTo'
import { useCalls } from '../api/listCalls'
import { useCreateCall } from '../hooks/useCreateCall'
import { useDebouncedValue } from '../hooks/useDebouncedValue'
import { callsUiStore } from '../store'
import { CallListItem } from './CallListItem'
import { HomeActions } from './HomeActions'

const SEARCH_DEBOUNCE_MS = 300

// kept across the remounts of the list (the home and a call are two routes)
let savedScrollTop = 0

export const CallsSearchField = () => {
  const { t } = useTranslation('calls', { keyPrefix: 'list' })
  const { search } = useSnapshot(callsUiStore)
  return (
    <SearchField
      value={search}
      onChange={(value) => {
        callsUiStore.search = value
      }}
      aria-label={t('searchLabel')}
      className={css({
        position: 'relative',
        display: 'flex',
        alignItems: 'center',
        '& input::-webkit-search-cancel-button': { display: 'none' },
        '&[data-empty] button': { display: 'none' },
      })}
    >
      <RiSearchLine
        size={18}
        aria-hidden="true"
        className={css({
          position: 'absolute',
          left: '0.75rem',
          color: 'fg.muted',
          pointerEvents: 'none',
        })}
      />
      <Input
        placeholder={t('search')}
        className={css({
          width: '100%',
          height: '2.5rem',
          paddingLeft: '2.5rem',
          paddingRight: '2.25rem',
          borderRadius: '12px',
          border: '1px solid transparent',
          backgroundColor: 'surface.hover',
          color: 'fg',
          fontSize: '0.9375rem',
          transition: 'border-color 150ms, background 150ms',
          _placeholder: { color: 'fg.muted' },
          '&[data-hovered]': { borderColor: 'border' },
          '&[data-focused]': {
            borderColor: 'accent',
            backgroundColor: 'surface.raised',
          },
        })}
      />
      <RACButton
        aria-label={t('clearSearch')}
        className={css({
          position: 'absolute',
          right: '0.375rem',
          display: 'flex',
          padding: '0.25rem',
          borderRadius: 'full',
          color: 'fg.muted',
          cursor: 'pointer',
          '&[data-hovered]': {
            backgroundColor: 'surface.pressed',
            color: 'fg',
          },
        })}
      >
        <RiCloseLine size={18} aria-hidden="true" />
      </RACButton>
    </SearchField>
  )
}

const CenteredState = ({ children }: { children: React.ReactNode }) => (
  <div
    className={css({
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      gap: '0.75rem',
      paddingX: '1.5rem',
      paddingY: '2.5rem',
      textAlign: 'center',
    })}
  >
    {children}
  </div>
)

/**
 * The calls of the user: a title, a button that starts a call, the search,
 * then the calls, loaded page by page while scrolling.
 */
export const CallListPanel = ({
  selectedCallId,
}: {
  selectedCallId?: string
}) => {
  const { t } = useTranslation('calls', { keyPrefix: 'list' })
  const { search } = useSnapshot(callsUiStore)
  const query = useDebouncedValue(search.trim(), SEARCH_DEBOUNCE_MS)
  const callsQuery = useCalls(query)
  const { startCall, isPending: isCreating } = useCreateCall()

  const calls = useMemo(
    () => callsQuery.data?.pages.flatMap((page) => page.results) ?? [],
    [callsQuery.data]
  )
  // labels are relative to today; the list refreshes every 30 s
  const now = new Date()

  const listRef = useRef<HTMLDivElement>(null)
  const hasCalls = calls.length > 0
  useLayoutEffect(() => {
    if (!hasCalls || !savedScrollTop) return
    // the rows of the collection may come a frame after the list
    let frame = 0
    let attempts = 0
    const restore = () => {
      const element = listRef.current
      if (!element) return
      element.scrollTop = savedScrollTop
      if (element.scrollTop < savedScrollTop && ++attempts < 10) {
        frame = requestAnimationFrame(restore)
      }
    }
    restore()
    return () => cancelAnimationFrame(frame)
  }, [hasCalls])
  // another search starts at the top
  const previousQuery = useRef(query)
  useLayoutEffect(() => {
    if (previousQuery.current === query) return
    previousQuery.current = query
    savedScrollTop = 0
    if (listRef.current) listRef.current.scrollTop = 0
  }, [query])

  return (
    <section
      aria-labelledby="calls-list-heading"
      className={css({
        display: 'flex',
        flexDirection: 'column',
        minHeight: 0,
        flex: 1,
        backgroundColor: 'surface.page',
        md: { borderRight: '1px solid {colors.border.subtle}' },
      })}
    >
      <div
        className={css({
          display: 'flex',
          flexDirection: 'column',
          gap: '0.875rem',
          paddingX: '1rem',
          paddingTop: '1rem',
          paddingBottom: '0.75rem',
        })}
      >
        <div
          className={css({
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '0.5rem',
          })}
        >
          <h2
            id="calls-list-heading"
            className={css({
              fontSize: '1.375rem',
              fontWeight: 700,
              lineHeight: 1.2,
              paddingLeft: '0.25rem',
            })}
          >
            {t('title')}
          </h2>
          <Button
            variant="primary"
            square
            aria-label={t('newCall')}
            tooltip={t('newCall')}
            isDisabled={isCreating}
            onPress={startCall}
            className={css({ borderRadius: '12px!' })}
          >
            <RiAddLine size={22} aria-hidden="true" />
          </Button>
        </div>
        <div className={css({ md: { display: 'none' } })}>
          <HomeActions compact />
        </div>
        <CallsSearchField />
      </div>
      {callsQuery.isLoading ? (
        <CenteredState>
          <Spinner size={28} />
          <Text variant="smNote" margin={false}>
            {t('loading')}
          </Text>
        </CenteredState>
      ) : callsQuery.isError && !hasCalls ? (
        <CenteredState>
          <Text variant="sm" margin={false} role="alert">
            {t('error')}
          </Text>
          <Button
            variant="secondary"
            size="sm"
            onPress={() => callsQuery.refetch()}
          >
            {t('retry')}
          </Button>
        </CenteredState>
      ) : (
        <GridList
          ref={listRef}
          aria-label={t('label')}
          onScroll={(event) => {
            savedScrollTop = event.currentTarget.scrollTop
          }}
          renderEmptyState={() => (
            <CenteredState>
              <Text variant="smNote" margin={false} wrap="balance">
                {query ? t('noResults') : t('empty')}
              </Text>
            </CenteredState>
          )}
          className={css({
            flex: 1,
            minHeight: 0,
            overflowY: 'auto',
            display: 'flex',
            flexDirection: 'column',
            gap: '0.125rem',
            paddingX: '0.5rem',
            paddingBottom: '1rem',
            outline: 'none',
          })}
        >
          {calls.map((call) => (
            <CallListItem
              key={call.id}
              call={call}
              now={now}
              isCurrent={call.id === selectedCallId}
              onOpen={(callId) => navigateTo('call', callId)}
            />
          ))}
          {callsQuery.hasNextPage && (
            <GridListLoadMoreItem
              onLoadMore={() => {
                if (!callsQuery.isFetchingNextPage) callsQuery.fetchNextPage()
              }}
              isLoading={callsQuery.isFetchingNextPage}
              className={css({
                display: 'flex',
                justifyContent: 'center',
                paddingY: '0.75rem',
              })}
            >
              <Spinner size={22} />
            </GridListLoadMoreItem>
          )}
        </GridList>
      )}
    </section>
  )
}
