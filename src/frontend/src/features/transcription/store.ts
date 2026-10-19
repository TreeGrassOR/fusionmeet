import { proxy } from 'valtio'

type State = {
  // Which action failed, shown in the error dialog. Empty when closed.
  errorDialog: '' | 'start' | 'stop'
}

export const transcriptionStore = proxy<State>({
  errorDialog: '',
})
