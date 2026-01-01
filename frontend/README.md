# TurkAnime Frontend

Next.js frontend for TurkAnime GUI application.

## Getting Started

1. Install dependencies:
```bash
npm install
```

2. Set up environment variables:
Create a `.env.local` file:
```
NEXT_PUBLIC_API_URL=http://localhost:8000
```

3. Start the development server:
```bash
npm run dev
```

4. Open [http://localhost:3000](http://localhost:3000) in your browser.

## Features

- Browse current season anime from LiveChart.me
- Search for anime
- View anime details with Japanese, Romaji, and English titles
- AniList integration for progress tracking
- Responsive design with Tailwind CSS

## Tech Stack

- Next.js 16
- TypeScript
- Tailwind CSS
- React

## API Integration

The frontend connects to the Python backend at `http://localhost:8000` by default.
Make sure the backend server is running before starting the frontend.

