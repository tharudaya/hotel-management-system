import type { ReactNode } from 'react'

/** Placeholder sections: the admin screens themselves are built in later steps. */
function AdminSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section aria-labelledby="admin-section" className="rounded-lg bg-white p-6 shadow-sm">
      <h1 id="admin-section" className="text-xl font-semibold text-slate-900">
        {title}
      </h1>
      <p className="mt-2 text-slate-600">{children}</p>
    </section>
  )
}

export function AdminAuditPage() {
  return <AdminSection title="Audit log">The audit log will be shown here.</AdminSection>
}
