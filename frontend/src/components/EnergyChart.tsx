// 17 bars from hour 6 to hour 22 (6am–10pm)
export default function EnergyChart({ curve }: { curve: number[] }) {
  const bars = curve.slice(6, 23)
  return (
    <div>
      <div className="flex gap-0.5 items-end h-9 my-2.5">
        {bars.map((v, i) => {
          const h = Math.round(v * 34)
          const bg =
            v > 0.72 ? '#2A6090' :
            v > 0.48 ? '#7AAFD4' :
            v > 0.25 ? '#B8D0E8' : '#DDE9F4'
          return (
            <div key={i} className="flex-1 rounded-t-sm" style={{ height: Math.max(h, 3), background: bg }} />
          )
        })}
      </div>
      <div className="flex justify-between">
        {['6am','9am','12pm','3pm','6pm','10pm'].map(l => (
          <span key={l} className="text-[10px] text-gray-text">{l}</span>
        ))}
      </div>
    </div>
  )
}
