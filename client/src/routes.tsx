import type { RouteObject } from 'react-router'

import { ProtectedRoute } from './auth/ProtectedRoute'
import { AppLayout } from './components/AppLayout'
import { ErrorPage } from './components/ErrorPage'
import { DashboardPage } from './pages/DashboardPage'
import { LoginPage } from './pages/LoginPage'

/** The route table, shared by the browser router (main.tsx) and tests (memory router). */
export const routes: RouteObject[] = [
  {
    // Catches errors thrown while rendering any route below.
    errorElement: <ErrorPage />,
    children: [
      { path: '/login', element: <LoginPage /> },
      {
        element: <ProtectedRoute />,
        children: [
          {
            element: <AppLayout />,
            children: [{ index: true, element: <DashboardPage /> }],
          },
        ],
      },
      { path: '*', element: <ErrorPage notFound /> },
    ],
  },
]
