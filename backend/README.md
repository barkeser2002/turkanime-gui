# TurkAnime Backend

Python FastAPI backend for anime data and progress tracking.

## Features

- **LiveChart.me Integration**: Fetch current season anime and trending content
- **Title Matching**: Advanced algorithm for matching Japanese, Romaji, and English titles
- **AniList Integration**: User progress tracking and OAuth2 authentication
- **REST API**: Clean and well-documented endpoints

## Installation

1. Install Python 3.9 or higher

2. Install dependencies:
```bash
pip install -r requirements.txt
```

## Running the Server

### Option 1: Using the startup script
```bash
python start_server.py
```

### Option 2: Direct uvicorn
```bash
uvicorn server:app --reload --host 0.0.0.0 --port 8000
```

### Option 3: Using the server module
```bash
python -m backend.server
```

The server will start at `http://localhost:8000`

## API Documentation

Once the server is running, visit:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## API Endpoints

### Anime Endpoints

- `GET /api/anime/current-season` - Get current season anime from LiveChart.me
- `GET /api/anime/news` - Get anime news feed
- `GET /api/anime/recently-aired` - Get recently aired episodes
- `GET /api/anime/search?q=<query>&source=<source>` - Search anime
- `GET /api/anime/details/{anime_id}` - Get detailed anime information

### Title Matching Endpoints

- `POST /api/titles/match` - Find best matching title from candidates
- `POST /api/titles/normalize` - Normalize a title for comparison

### AniList Endpoints

- `GET /api/anilist/user` - Get current authenticated user
- `GET /api/anilist/list` - Get user's anime list
- `POST /api/anilist/progress` - Update anime progress
- `GET /api/anilist/auth/url` - Get OAuth authorization URL
- `POST /api/anilist/auth/token` - Exchange OAuth code for token

## Testing

Run the test suite:
```bash
python test_backend.py
```

## Configuration

Edit `config.py` to change:
- API host and port
- LiveChart.me endpoints
- Request timeouts

## Development

The backend uses:
- **FastAPI** - Modern web framework
- **BeautifulSoup4** - HTML parsing
- **Requests** - HTTP client
- **Feedparser** - RSS feed parsing
- **Uvicorn** - ASGI server

### Adding New Endpoints

1. Add your endpoint function to `server.py`
2. Use FastAPI decorators (`@app.get`, `@app.post`, etc.)
3. Add type hints using Pydantic models
4. Document with docstrings

Example:
```python
@app.get("/api/example")
async def example_endpoint():
    """Example endpoint description"""
    return {"success": True, "data": "example"}
```

## CORS Configuration

The backend is configured to allow requests from:
- `http://localhost:3000` (Next.js default)
- `http://127.0.0.1:3000`

To add more origins, edit the `allow_origins` list in `server.py`.

## Error Handling

All endpoints return responses in the format:
```json
{
  "success": true,
  "data": { ... }
}
```

Or on error:
```json
{
  "detail": "Error message"
}
```

## Dependencies

- fastapi>=0.104.0
- uvicorn[standard]>=0.24.0
- pydantic>=2.0.0
- requests>=2.31.0
- beautifulsoup4>=4.12.0
- feedparser>=6.0.10
