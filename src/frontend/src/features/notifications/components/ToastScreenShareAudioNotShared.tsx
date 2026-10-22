import { useToast } from 'react-aria'
import { useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { RiCloseLine } from '@remixicon/react'
import { HStack, VStack } from '@/styled-system/jsx'
import { css } from '@/styled-system/css'
import { Button } from '@/primitives'
import { type ToastProps } from './Toast'
import { StyledToastContainer } from './StyledToastContainer'

/**
 * Shown to the sharer when the screen share audio was not published because
 * it may carry the conference itself (see startScreenShare).
 */
export function ToastScreenShareAudioNotShared({
  state,
  ...props
}: Readonly<ToastProps>) {
  const { t } = useTranslation('screenShare', {
    keyPrefix: 'audioNotShared',
    useSuspense: false,
  })
  const ref = useRef(null)
  const {
    toastProps,
    contentProps,
    titleProps,
    descriptionProps,
    closeButtonProps,
  } = useToast(props, state, ref)
  const isUnsupported = props.toast.content?.reason === 'unsupported'

  return (
    <StyledToastContainer {...toastProps} ref={ref}>
      <HStack alignItems="start" gap={0.5} padding={14} maxWidth="400px">
        <VStack alignItems="start" gap={0.25} {...contentProps}>
          <p {...titleProps} className={css({ fontWeight: 'bold' })}>
            {t('title')}
          </p>
          <p {...descriptionProps} className={css({ textStyle: 'sm' })}>
            {t('description')}
            {isUnsupported && <> {t('updateBrowser')}</>}
          </p>
        </VStack>
        <Button square size="sm" invisible {...closeButtonProps}>
          <RiCloseLine color="white" aria-hidden />
        </Button>
      </HStack>
    </StyledToastContainer>
  )
}
