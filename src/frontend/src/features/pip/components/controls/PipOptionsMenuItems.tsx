import { Menu as RACMenu, MenuItem } from 'react-aria-components'
import { PictureInPictureMenuItem } from '@/features/rooms/livekit/components/controls/Options/PictureInPictureMenuItem'
import { CollapsibleControl, CollapsibleControls } from '../PipControlBar'
import { RiArrowUpLine, RiEmotionLine, RiHand } from '@remixicon/react'
import { menuRecipe } from '@/primitives/menuRecipe.ts'
import { useReactionsToolbar } from '@/features/reactions/hooks/useReactionsToolbar'
import { useRoomContext } from '@livekit/components-react'
import { useRaisedHand } from '@/features/rooms/livekit/hooks/useRaisedHand'
import { useTranslation } from 'react-i18next'
import { useScreenShareToggle } from '@/features/rooms/livekit/hooks/useScreenShareToggle'

type PipOverflowItemsProps = {
  overflowControls: Set<CollapsibleControl>
}

export const PipOptionsMenuItems = ({
  overflowControls,
}: PipOverflowItemsProps) => {
  const { t } = useTranslation('rooms')
  const room = useRoomContext()
  const { isHandRaised, toggleRaisedHand } = useRaisedHand({
    participant: room.localParticipant,
  })
  const { toggle: toggleScreenShare, enabled: isScreenSharing } =
    useScreenShareToggle()
  const { toggle: toggleReactions } = useReactionsToolbar()
  const itemClass = menuRecipe({ icon: true, variant: 'dark' }).item

  return (
    <RACMenu
      style={{
        minWidth: '150px',
        width: '300px',
      }}
    >
      <PictureInPictureMenuItem />
      {overflowControls.has(CollapsibleControls.REACTIONS) && (
        <MenuItem onAction={toggleReactions} className={itemClass}>
          <RiEmotionLine size={20} />
          {t('controls.reactions.button')}
        </MenuItem>
      )}
      {overflowControls.has(CollapsibleControls.SCREEN_SHARE) && (
        <MenuItem
          onAction={() => void toggleScreenShare()}
          className={itemClass}
        >
          <RiArrowUpLine size={20} />
          {t(
            isScreenSharing
              ? 'controls.screenShare.stop'
              : 'controls.screenShare.start'
          )}
        </MenuItem>
      )}
      {overflowControls.has(CollapsibleControls.HAND) && (
        <MenuItem onAction={toggleRaisedHand} className={itemClass}>
          <RiHand size={20} />
          {isHandRaised ? t('controls.hand.lower') : t('controls.hand.raise')}
        </MenuItem>
      )}
    </RACMenu>
  )
}
