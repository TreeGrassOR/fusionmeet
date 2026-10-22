import { ChatRow } from '@/stores/chat'
import React, { useMemo } from 'react'
import { formatChatMessageLinks } from '../utils'
import { css } from '@/styled-system/css'
import { Text } from '@/primitives'

type ChatMessageBodyProps = Pick<ChatRow, 'message'>

export const ChatMessageBody = React.memo(
  ({ message }: ChatMessageBodyProps) => {
    const formattedMessage = useMemo(() => {
      return formatChatMessageLinks(message)
    }, [message])

    return (
      <Text
        variant="sm"
        margin={false}
        className={css({
          whiteSpace: 'pre-wrap',
          '& .lk-chat-link': {
            color: { base: 'blue', _dark: 'accent.text' },
            textDecoration: 'underline',
          },
        })}
      >
        {formattedMessage}
      </Text>
    )
  }
)

ChatMessageBody.displayName = 'ChatMessageBody'
