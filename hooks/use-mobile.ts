import * as React from "react"

const MOBILE_BREAKPOINT = 940

export function useIsMobile() {
  // undefined (no boolean) a propósito. Leer window.innerWidth acá mismo,
  // en el inicializador de useState, corre durante el RENDER, no en un
  // efecto -- en el servidor da false (sin window), pero en el cliente
  // corre con el viewport real ya en la primera pasada de hidratación. Con
  // un viewport angosto (<940px) eso hacía que el cliente calculara
  // isMobile=true mientras el servidor asumió false: un mismatch real de
  // hidratación (bug encontrado 2026-08-14). El valor real recién se
  // calcula en el useEffect de abajo -- que por definición corre solo en
  // el cliente, después de que la hidratación ya terminó -- así el primer
  // render del cliente siempre coincide con el del servidor.
  const [isMobile, setIsMobile] = React.useState<boolean | undefined>(undefined)

  React.useEffect(() => {
    const mql = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`)
    const onChange = () => {
      setIsMobile(window.innerWidth < MOBILE_BREAKPOINT)
    }
    mql.addEventListener("change", onChange)
    onChange()
    return () => mql.removeEventListener("change", onChange)
  }, [])

  return !!isMobile
}
