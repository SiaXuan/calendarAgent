/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ice:  '#EEF4F9',
        ice2: '#DDE9F4',
        steel:'#B8D0E8',
        blue: { DEFAULT:'#7AAFD4', mid:'#4E8BB5', deep:'#2A6090', text:'#1A4A72' },
        amber:{ DEFAULT:'#D4943A', bg:'#FDF4E8', border:'#E8C07A' },
        'gray-border':'#D8DDE3',
        'gray-text':  '#8A939E',
      },
      fontFamily: { sans: ['-apple-system','BlinkMacSystemFont','SF Pro Text','Segoe UI','sans-serif'] }
    }
  },
  plugins: []
}
