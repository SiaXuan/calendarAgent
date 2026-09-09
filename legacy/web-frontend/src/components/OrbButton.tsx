import { useEffect, useRef, useState } from 'react'

function lerp(a: number[], b: number[], f: number) {
  return [a[0]+(b[0]-a[0])*f, a[1]+(b[1]-a[1])*f, a[2]+(b[2]-a[2])*f]
}
function rgb(c: number[]) {
  return `rgb(${Math.round(c[0])},${Math.round(c[1])},${Math.round(c[2])})`
}

const IDLE_PAL  = [[180,210,235],[120,175,212],[78,139,181],[42,96,144],[26,74,114]]
const ACTIVE_PAL= [[100,160,210],[60,120,180],[30,85,150],[15,60,120],[8,40,95]]

export default function OrbButton({ onPress }: { onPress?: () => void }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const stateRef  = useRef({ t: 0, listening: false, speed: 0.008, targetSpeed: 0.008 })
  const rafRef    = useRef<number>(0)
  const [active, setActive] = useState(false)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')!
    const W = 48, CX = 24, CY = 24

    function draw() {
      const s = stateRef.current
      s.speed += (s.targetSpeed - s.speed) * 0.05
      s.t += s.speed

      ctx.clearRect(0, 0, W, W)
      ctx.save()
      ctx.beginPath()
      ctx.arc(CX, CY, CX, 0, Math.PI * 2)
      ctx.clip()

      const pal   = s.listening ? ACTIVE_PAL : IDLE_PAL
      const pulse = s.listening
        ? Math.sin(s.t * 6) * 0.5 + 0.5
        : Math.sin(s.t * 1.8) * 0.5 + 0.5
      const sc = s.listening ? 1 + pulse * 0.06 : 1 + pulse * 0.03

      ctx.save()
      ctx.translate(CX, CY)
      ctx.scale(sc, sc)
      ctx.translate(-CX, -CY)

      const g = ctx.createRadialGradient(CX-4, CY-6, 2, CX, CY, CX)
      g.addColorStop(0, rgb(lerp(pal[0], pal[1], pulse)))
      g.addColorStop(0.45, rgb(lerp(pal[2], pal[3], pulse)))
      g.addColorStop(1, rgb(pal[4]))
      ctx.fillStyle = g
      ctx.fillRect(0, 0, W, W)

      const nw = s.listening ? 5 : 3
      for (let i = 0; i < nw; i++) {
        const ph  = s.t * (s.listening ? 2.2 : 1.1) + (i * Math.PI * 2 / nw)
        const amp = s.listening ? 3 + pulse * 5 : 2 + pulse * 3
        const fy  = CY + Math.sin(ph) * amp
        const wc  = lerp(pal[1], pal[0], i / nw)
        const a   = s.listening ? 0.35 : 0.2
        const wg  = ctx.createLinearGradient(0, 0, W, 0)
        const rc  = `rgba(${Math.round(wc[0])},${Math.round(wc[1])},${Math.round(wc[2])}`
        wg.addColorStop(0, `${rc},0)`)
        wg.addColorStop(0.5, `${rc},${a})`)
        wg.addColorStop(1, `${rc},0)`)
        ctx.beginPath()
        ctx.moveTo(0, fy)
        for (let x = 0; x <= W; x += 2) ctx.lineTo(x, fy + Math.sin(x * 0.18 + ph + i) * amp)
        ctx.lineTo(W, W); ctx.lineTo(0, W); ctx.closePath()
        ctx.fillStyle = wg
        ctx.fill()
      }

      const gr = ctx.createRadialGradient(CX-3, CY-5, 0, CX-3, CY-5, CX * 0.7)
      const glowA = s.listening ? 0.55 : 0.35 + pulse * 0.15
      gr.addColorStop(0, `rgba(220,238,255,${glowA})`)
      gr.addColorStop(1, 'rgba(220,238,255,0)')
      ctx.fillStyle = gr
      ctx.fillRect(0, 0, W, W)

      ctx.restore()
      ctx.restore()
      rafRef.current = requestAnimationFrame(draw)
    }

    draw()
    return () => cancelAnimationFrame(rafRef.current)
  }, [])

  const start = () => {
    stateRef.current.listening = true
    stateRef.current.targetSpeed = 0.032
    setActive(true)
  }
  const stop = () => {
    stateRef.current.listening = false
    stateRef.current.targetSpeed = 0.008
    setActive(false)
    onPress?.()
  }

  return (
    <button
      className="relative w-12 h-12 rounded-full flex items-center justify-center select-none outline-none"
      style={{ transform: active ? 'scale(1.1)' : 'scale(1)', transition: 'transform 0.15s' }}
      onMouseDown={start} onMouseUp={stop} onTouchStart={start} onTouchEnd={stop}
    >
      <canvas ref={canvasRef} width={48} height={48} className="absolute inset-0 rounded-full" />
      <svg className="relative z-10 w-5 h-5" viewBox="0 0 24 24" fill="none"
        stroke="white" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z"/>
        <path d="M19 10v2a7 7 0 01-14 0v-2M12 19v4M8 23h8"/>
      </svg>
    </button>
  )
}
