import React from 'react'
import Snackbar from '@mui/material/Snackbar'
import Alert from '@mui/material/Alert'

import { useToastStore } from '../state/toastStore'

export default function AppToast() {
  const open = useToastStore((state) => state.open)
  const message = useToastStore((state) => state.message)
  const tone = useToastStore((state) => state.tone)
  const hideToast = useToastStore((state) => state.hideToast)

  return (
    <Snackbar
      open={open}
      autoHideDuration={3000}
      onClose={hideToast}
      anchorOrigin={{ vertical: 'top', horizontal: 'center' }}
      sx={{
        zIndex: 40000,
        top: '28px !important',
      }}
    >
      <Alert onClose={hideToast} severity={tone} variant="filled" sx={{ width: '100%' }}>
        {message}
      </Alert>
    </Snackbar>
  )
}
