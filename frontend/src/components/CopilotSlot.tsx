import type { ReactNode } from 'react'

/** PAN-48 integration point: pass the existing panel as children, with no layout changes. */
export function CopilotSlot({ children }: { children: ReactNode }) {
  return (
    <section className="copilot-slot" aria-label="AI Copilot">
      {children}
    </section>
  )
}
