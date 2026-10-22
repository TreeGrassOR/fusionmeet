import { Div, ToggleButton } from '@/primitives'
import { RiArrowUpLine, RiCloseFill, RiRectangleLine } from '@remixicon/react'
import { useTranslation } from 'react-i18next'
import { type UseTrackToggleProps } from '@livekit/components-react'
import { Track } from 'livekit-client'
import { type ButtonRecipeProps } from '@/primitives/buttonRecipe'
import { ToggleButtonProps } from '@/primitives/ToggleButton'
import { TrackSource } from '@livekit/protocol'
import { useCanPublishTrack } from '@/features/rooms/livekit/hooks/useCanPublishTrack'
import { useScreenShareToggle } from '@/features/rooms/livekit/hooks/useScreenShareToggle'

type Props = Omit<
  UseTrackToggleProps<Track.Source.ScreenShare>,
  'source' | 'captureOptions' | 'publishOptions' | 'initialState'
> &
  Pick<NonNullable<ButtonRecipeProps>, 'variant'> &
  ToggleButtonProps

export const ScreenShareToggle = ({
  variant = 'primaryDark',
  onPress,
  onChange,
  onDeviceError,
  ...props
}: Props) => {
  const { t } = useTranslation('rooms', { keyPrefix: 'controls.screenShare' })
  const { enabled, isPending, toggle } = useScreenShareToggle({
    onChange,
    onDeviceError,
  })

  const tooltipLabel = enabled ? 'stop' : 'start'
  const Icon = enabled ? RiCloseFill : RiArrowUpLine

  const canShareScreen = useCanPublishTrack(TrackSource.SCREEN_SHARE)

  // fixme - remove ToggleButton custom styles when we design a proper icon
  return (
    <ToggleButton
      isSelected={enabled}
      isDisabled={!canShareScreen || isPending}
      square
      variant={variant}
      aria-label={t(tooltipLabel)}
      tooltip={t(tooltipLabel)}
      onPress={(e) => {
        void toggle()
        onPress?.(e)
      }}
      data-attr={`controls-screenshare-${tooltipLabel}`}
      {...props}
    >
      <Div position="relative">
        <RiRectangleLine size={24} />
        <Icon
          size={14}
          style={{
            position: 'absolute',
            top: '50%',
            left: '50%',
            transform: 'translate(-50%, -50%)',
          }}
        />
      </Div>
    </ToggleButton>
  )
}
