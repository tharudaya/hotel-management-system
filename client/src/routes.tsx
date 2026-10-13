import type { RouteObject } from 'react-router'

import { AdminIndex, AdminLayout } from './admin/AdminLayout'
import { AuditLogPage } from './admin/auditlog/AuditLogPage'
import { NewRolePage } from './admin/roles/NewRolePage'
import { RoleDetailPage } from './admin/roles/RoleDetailPage'
import { RolesListPage } from './admin/roles/RolesListPage'
import { RoomsLayout } from './admin/rooms/RoomsLayout'
import { RoomsPage } from './admin/rooms/RoomsPage'
import { RoomTypesPage } from './admin/rooms/RoomTypesPage'
import { NewUserPage } from './admin/users/NewUserPage'
import { UserDetailPage } from './admin/users/UserDetailPage'
import { UsersListPage } from './admin/users/UsersListPage'
import { ADMIN_PERMISSIONS, P } from './auth/permissions'
import { ProtectedRoute } from './auth/ProtectedRoute'
import { RequirePermission } from './auth/RequirePermission'
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
      // Links carry the token in the fragment: /activate#<token>.
      { path: '/activate', element: <SetPasswordPage kind="activate" /> },
      { path: '/reset-password', element: <SetPasswordPage kind="reset-password" /> },
      {
        element: <ProtectedRoute />,
        children: [
          {
            element: <AppLayout />,
            children: [
              { index: true, element: <DashboardPage /> },
              {
                path: '/admin',
                element: (
                  <RequirePermission anyOf={ADMIN_PERMISSIONS}>
                    <AdminLayout />
                  </RequirePermission>
                ),
                children: [
                  { index: true, element: <AdminIndex /> },
                  {
                    path: 'users',
                    element: <RequirePermission permission={P.USER_VIEW} />,
                    children: [
                      { index: true, element: <UsersListPage /> },
                      {
                        path: 'new',
                        element: (
                          <RequirePermission permission={P.USER_MANAGE}>
                            <NewUserPage />
                          </RequirePermission>
                        ),
                      },
                      { path: ':userId', element: <UserDetailPage /> },
                    ],
                  },
                  {
                    path: 'roles',
                    element: <RequirePermission permission={P.ROLE_VIEW} />,
                    children: [
                      { index: true, element: <RolesListPage /> },
                      {
                        path: 'new',
                        element: (
                          <RequirePermission permission={P.ROLE_MANAGE}>
                            <NewRolePage />
                          </RequirePermission>
                        ),
                      },
                      { path: ':roleId', element: <RoleDetailPage /> },
                    ],
                  },
                  {
                    path: 'rooms',
                    element: (
                      <RequirePermission permission={P.ROOM_MANAGE}>
                        <RoomsLayout />
                      </RequirePermission>
                    ),
                    children: [
                      { index: true, element: <RoomsPage /> },
                      { path: 'types', element: <RoomTypesPage /> },
                    ],
                  },
                  {
                    path: 'audit',
                    element: (
                      <RequirePermission permission={P.AUDIT_VIEW}>
                        <AuditLogPage />
                      </RequirePermission>
                    ),
                  },
                ],
              },
            ],
          },
        ],
      },
      { path: '*', element: <ErrorPage notFound /> },
    ],
  },
]
