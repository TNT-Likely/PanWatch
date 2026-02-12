// Global ambient background for the whole app.
// Keep it subtle in light mode; slightly stronger in dark mode.
export default function AmbientBackground() {
  return (
    <div className="pointer-events-none absolute inset-0 -z-10 overflow-hidden">
      {/* Calm top gradient, masked to avoid content interference */}
      <div className="absolute inset-0 [mask-image:linear-gradient(to_bottom,black,transparent_68%)]">
        <div className="absolute inset-0 bg-[radial-gradient(900px_circle_at_20%_-10%,hsl(var(--primary)/0.10),transparent_60%),radial-gradient(900px_circle_at_80%_0%,hsl(var(--success)/0.06),transparent_62%)]" />
        <div className="absolute inset-0 opacity-[0.06] dark:opacity-[0.04] mix-blend-overlay [background-image:repeating-linear-gradient(0deg,rgba(255,255,255,0.03)_0px,rgba(255,255,255,0.03)_1px,transparent_1px,transparent_4px)]" />
      </div>
    </div>
  )
}
