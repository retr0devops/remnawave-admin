import { useMemo, useEffect, useState } from 'react'
import { useAppearanceStore } from '../store/useAppearanceStore'

/**
 * Reads a CSS custom property value from :root / <html>.
 */
function getCSSVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

/**
 * Converts a hex color to HSL components.
 */
function hexToHSL(hex: string): { h: number; s: number; l: number } | null {
  const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex)
  if (!result) return null
  const r = parseInt(result[1], 16) / 255
  const g = parseInt(result[2], 16) / 255
  const b = parseInt(result[3], 16) / 255
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  const l = (max + min) / 2
  if (max === min) return { h: 0, s: 0, l }
  const d = max - min
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min)
  let h = 0
  if (max === r) h = ((g - b) / d + (g < b ? 6 : 0)) / 6
  else if (max === g) h = ((b - r) / d + 2) / 6
  else h = ((r - g) / d + 4) / 6
  return { h: h * 360, s, l }
}

/**
 * Converts HSL to hex color string.
 */
function hslToHex(h: number, s: number, l: number): string {
  const hue2rgb = (p: number, q: number, t: number) => {
    if (t < 0) t += 1
    if (t > 1) t -= 1
    if (t < 1 / 6) return p + (q - p) * 6 * t
    if (t < 1 / 2) return q
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6
    return p
  }
  const hNorm = h / 360
  if (s === 0) {
    const v = Math.round(l * 255)
    return `#${v.toString(16).padStart(2, '0')}${v.toString(16).padStart(2, '0')}${v.toString(16).padStart(2, '0')}`
  }
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s
  const p = 2 * l - q
  const r = Math.round(hue2rgb(p, q, hNorm + 1 / 3) * 255)
  const g = Math.round(hue2rgb(p, q, hNorm) * 255)
  const b = Math.round(hue2rgb(p, q, hNorm - 1 / 3) * 255)
  return `#${r.toString(16).padStart(2, '0')}${g.toString(16).padStart(2, '0')}${b.toString(16).padStart(2, '0')}`
}

/**
 * Generates a palette of N colors based on a base color with varying lightness.
 */
function generatePalette(baseHex: string, count: number): string[] {
  const hsl = hexToHSL(baseHex)
  if (!hsl) return Array(count).fill(baseHex)
  const colors: string[] = []
  for (let i = 0; i < count; i++) {
    const lightness = 0.35 + (i / (count - 1 || 1)) * 0.35
    colors.push(hslToHex(hsl.h, Math.min(hsl.s * 1.1, 1), lightness))
  }
  return colors
}

/**
 * Returns chart colors that adapt to the current theme (light/dark + accent).
 * Uses CSS custom properties defined in index.css so colors stay in sync.
 */
export function useChartTheme() {
  const colorMode = useAppearanceStore((s) => s.colorMode)
  const theme = useAppearanceStore((s) => s.theme)
  const isLight = colorMode === 'light'

  const [accentColor, setAccentColor] = useState('#06b6d4')

  useEffect(() => {
    // Small delay to let CSS variables apply after theme change
    const timer = setTimeout(() => {
      const raw = getCSSVar('--accent-from')
      if (raw) setAccentColor(raw)
    }, 50)
    return () => clearTimeout(timer)
  }, [theme, colorMode])

  return useMemo(
    () => ({
      axis: isLight ? '#475569' : '#8b949e',
      tick: isLight ? '#334155' : '#c9d1d9',
      grid: isLight ? 'rgba(148, 163, 184, 0.3)' : 'rgba(72, 79, 88, 0.3)',
      tooltipStyle: {
        backgroundColor: isLight ? 'rgba(255, 255, 255, 0.95)' : 'rgba(22, 27, 34, 0.95)',
        border: `1px solid ${isLight ? 'rgba(203, 213, 225, 0.6)' : 'rgba(72, 79, 88, 0.3)'}`,
        borderRadius: '8px',
        backdropFilter: 'blur(12px)',
        color: isLight ? '#1e293b' : '#c9d1d9',
      } as React.CSSProperties,
      tooltipTextClass: isLight ? 'text-slate-800' : 'text-dark-50',
      tooltipMutedClass: isLight ? 'text-slate-500' : 'text-muted-foreground',
      mapBackground: isLight ? '#e2e8f0' : '#0d1117',
      mapTileUrl: isLight
        ? 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png'
        : 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
      accentColor,
      nodeColors: generatePalette(accentColor, 10),
    }),
    [isLight, accentColor],
  )
}
