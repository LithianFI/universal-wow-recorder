/** Theme shared by all pages (was an inline tailwind.config in each template). */
module.exports = {
  content: ['./templates/**/*.html'],
  theme: {
    extend: {
      colors: {
        ink: { 900: '#0a0806', 800: '#0f0d0a', 700: '#141109', 600: '#1a1610', 500: '#221d15', 400: '#2e271d', 300: '#3d3529' },
        parchment: { 900: '#f0e6cd', 700: '#d4ccb6', 500: '#a89c82', 400: '#8a8270', 300: '#6e6552' },
        gold: { 500: '#c9a961', 400: '#d6b975', 600: '#a88a45' },
        diff: { mythic: '#ff8c1a', heroic: '#73adff', normal: '#87cf63', lfr: '#a89c82' },
      },
      fontFamily: {
        display: ['"IM Fell English"', 'Georgia', 'serif'],
        sans: ['Raleway', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['"Fira Mono"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
    },
  },
};
