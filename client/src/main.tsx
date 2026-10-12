import './index.css'

import { QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter } from 'react-router'
import { RouterProvider } from 'react-router/dom'

import { setUnauthorizedHandler } from './api/client'
import { createQueryClient } from './queryClient'
import { routes } from './routes'

const queryClient = createQueryClient()
const router = createBrowserRouter(routes)

// Session gone (expired, logged out elsewhere): forget cached server state and go to login.
setUnauthorizedHandler(() => {
  queryClient.clear()
  const { pathname, search, hash } = router.state.location
  if (pathname !== '/login') {
    void router.navigate('/login', {
      replace: true,
      state: { from: `${pathname}${search}${hash}` },
    })
  }
})

const root = document.getElementById('root')
if (!root) throw new Error('#root element missing from index.html')

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
)
