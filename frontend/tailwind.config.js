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
        stock: {
          up: '#E53935',
          down: '#43A047',
        },
      },
      borderRadius: {
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)',
      },
      keyframes: {
        'ai-chain-flow': {
          '0%': { transform: 'translateX(-120%)' },
          '100%': { transform: 'translateX(320%)' },
        },
        'ai-chain-pulse-hot': {
          '0%, 100%': { transform: 'scale(1)', boxShadow: '0 0 0 0 rgb(245 158 11 / 0.55)' },
          '50%': { transform: 'scale(1.28)', boxShadow: '0 0 0 8px rgb(245 158 11 / 0)' },
        },
        'ai-chain-pulse-next': {
          '0%, 100%': { transform: 'scale(1)', boxShadow: '0 0 0 0 rgb(249 115 22 / 0.45)' },
          '50%': { transform: 'scale(1.22)', boxShadow: '0 0 0 7px rgb(249 115 22 / 0)' },
        },
        'ai-chain-pulse-theme': {
          '0%, 100%': { transform: 'scale(1)', boxShadow: '0 0 0 0 rgb(16 185 129 / 0.45)' },
          '50%': { transform: 'scale(1.2)', boxShadow: '0 0 0 7px rgb(16 185 129 / 0)' },
        },
        'ai-chain-badge-hot': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.75' },
        },
        'ai-chain-badge-next': {
          '0%, 100%': { transform: 'translateY(0)' },
          '50%': { transform: 'translateY(-1px)' },
        },
        'ai-chain-badge-theme': {
          '0%, 100%': { opacity: '0.9' },
          '50%': { opacity: '1' },
        },
        'ai-chain-arrow': {
          '0%, 100%': { transform: 'translateX(0)', opacity: '0.5' },
          '50%': { transform: 'translateX(4px)', opacity: '1' },
        },
      },
      animation: {
        'ai-chain-flow': 'ai-chain-flow 2.8s ease-in-out infinite',
        'ai-chain-pulse-hot': 'ai-chain-pulse-hot 1.8s ease-in-out infinite',
        'ai-chain-pulse-next': 'ai-chain-pulse-next 2.2s ease-in-out infinite',
        'ai-chain-pulse-theme': 'ai-chain-pulse-theme 2.4s ease-in-out infinite',
        'ai-chain-badge-hot': 'ai-chain-badge-hot 1.8s ease-in-out infinite',
        'ai-chain-badge-next': 'ai-chain-badge-next 2s ease-in-out infinite',
        'ai-chain-badge-theme': 'ai-chain-badge-theme 2.4s ease-in-out infinite',
        'ai-chain-arrow': 'ai-chain-arrow 2s ease-in-out infinite',
      },
    },
  },
  plugins: [require('tailwindcss-animate'), require('@tailwindcss/typography')],
}
