import React from 'react'
import { createRoot } from 'react-dom/client'
import { ThemeProvider } from '@mui/material/styles'

import './index.scss'
import muiTheme from './MuiTheme'
import App from './App'
import { initializeLocale } from './i18n'

const container = document.getElementById('root')
const root = createRoot(container!)
void initializeLocale().then(() => root.render(
  <React.StrictMode>
    <ThemeProvider theme={muiTheme}>
      <App />
    </ThemeProvider>
  </React.StrictMode>
))
