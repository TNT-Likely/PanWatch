/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: [
    './index.html',
    './src/**/*.{ts,tsx}',
    './packages/**/*.{ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))',
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary))',
          foreground: 'hsl(var(--secondary-foreground))',
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          foreground: 'hsl(var(--destructive-foreground))',
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))',
        },
        accent: {
          DEFAULT: 'hsl(var(--accent))',
          foreground: 'hsl(var(--accent-foreground))',
        },
        card: {
          DEFAULT: 'hsl(var(--card))',
          foreground: 'hsl(var(--card-foreground))',
        },
        success: {
          DEFAULT: 'hsl(var(--success))',
          foreground: 'hsl(var(--success-foreground))',
        },
        warning: {
          DEFAULT: 'hsl(var(--warning))',
          foreground: 'hsl(var(--warning-foreground))',
        },
        // 涨跌语义色（红涨绿跌，随主题切换亮暗档）：价格/盈亏一律用 stock-up / stock-down
        stock: {
          up: 'hsl(var(--stock-up) / <alpha-value>)',
          down: 'hsl(var(--stock-down) / <alpha-value>)',
        },
        // 暖纸主题：选中/高亮带 + 柔和分类色签（fg-700 on bg-50 规范，详见 index.css）
        highlight: 'hsl(var(--highlight))',
        chip: {
          amber: { fg: 'hsl(var(--chip-amber-fg))', bg: 'hsl(var(--chip-amber-bg))' },
          sky: { fg: 'hsl(var(--chip-sky-fg))', bg: 'hsl(var(--chip-sky-bg))' },
          emerald: { fg: 'hsl(var(--chip-emerald-fg))', bg: 'hsl(var(--chip-emerald-bg))' },
          violet: { fg: 'hsl(var(--chip-violet-fg))', bg: 'hsl(var(--chip-violet-bg))' },
          rose: { fg: 'hsl(var(--chip-rose-fg))', bg: 'hsl(var(--chip-rose-bg))' },
          slate: { fg: 'hsl(var(--chip-slate-fg))', bg: 'hsl(var(--chip-slate-bg))' },
        },
        chart: {
          ma1: 'hsl(var(--chart-ma1))',
          ma2: 'hsl(var(--chart-ma2))',
          ma3: 'hsl(var(--chart-ma3))',
          baseline: 'hsl(var(--chart-baseline))',
        },
      },
      // 字阶（不绑定行高，与原任意值等价替换）：mini10 / caption11 / body-sm12 / body13 / body-lg14 / title16 / heading18 / headline20 / display24
      fontSize: {
        mini: '10px',
        caption: '11px',
        'body-sm': '12px',
        body: '13px',
        'body-lg': '14px',
        title: '16px',
        heading: '18px',
        headline: '20px',
        display: '24px',
        'display-lg': '34px',
      },
      borderRadius: {
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)',
      },
    },
  },
  plugins: [require('tailwindcss-animate'), require('@tailwindcss/typography')],
}
