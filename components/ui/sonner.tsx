"use client"

import { useTheme } from "next-themes"
import { Toaster as Sonner, type ToasterProps } from "sonner"
import { CircleCheckIcon, InfoIcon, TriangleAlertIcon, OctagonXIcon, Loader2Icon } from "lucide-react"

const Toaster = ({ ...props }: ToasterProps) => {
  const { theme = "system" } = useTheme()

  return (
    <Sonner
      theme={theme as ToasterProps["theme"]}
      position="top-center"
      className="toaster group"
      icons={{
        success: (
          <CircleCheckIcon className="size-4" />
        ),
        info: (
          <InfoIcon className="size-4" />
        ),
        warning: (
          <TriangleAlertIcon className="size-4" />
        ),
        error: (
          <OctagonXIcon className="size-4" />
        ),
        loading: (
          <Loader2Icon className="size-4 animate-spin" />
        ),
      }}
      richColors
      style={
        {
          "--normal-bg": "var(--popover)",
          "--normal-text": "var(--popover-foreground)",
          "--normal-border": "var(--border)",
          "--border-radius": "var(--radius)",
          "--success-bg": "var(--status-success-bg)",
          "--success-text": "var(--status-success-fg)",
          "--success-border": "color-mix(in srgb, var(--status-success-fg) 28%, transparent)",
          "--info-bg": "var(--status-info-bg)",
          "--info-text": "var(--status-info-fg)",
          "--info-border": "color-mix(in srgb, var(--status-info-fg) 28%, transparent)",
          "--warning-bg": "var(--status-warning-bg)",
          "--warning-text": "var(--status-warning-fg)",
          "--warning-border": "color-mix(in srgb, var(--status-warning-fg) 28%, transparent)",
          "--error-bg": "var(--status-destructive-bg)",
          "--error-text": "var(--status-destructive-fg)",
          "--error-border": "color-mix(in srgb, var(--status-destructive-fg) 28%, transparent)",
        } as React.CSSProperties
      }
      toastOptions={{
        classNames: {
          toast: "cn-toast",
        },
      }}
      {...props}
    />
  )
}

export { Toaster }
