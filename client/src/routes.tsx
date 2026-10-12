import type { RouteObject } from 'react-router'

import { ProtectedRoute } from './auth/ProtectedRoute'
import { AppLayout } from './components/AppLayout'
import { ErrorPage } from './components/ErrorPage'
import { DashboardPage } from './pages/DashboardPage'
import { ForgotPasswordPage } from './pages/ForgotPasswordPage'
import { LoginPage } from './pages/LoginPage'
import { SetPasswordPage } from './pages/SetPasswordPage'

/** The route table, shared by the browser router (main.tsx) and tests (memory router). */
export const routes: RouteObject[] = [
  {
    // Catches errors thrown while rendering any route below.
    errorElement: <ErrorPage />,
    children: [
      // Signed-out pages.
      { path: '/login', element: <LoginPage /> },
      { path: '/forgot-password', element: <ForgotPasswordPage /> },
      { path: '/activate/:token', element: <SetPasswordPage kind="activate" /> },
      { path: '/reset-password/:token', element: <SetPasswordPage kind="reset-password" /> },
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
