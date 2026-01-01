"""
Unified TurkAnime GUI Server
Serves both backend API and frontend static files from a single process
"""

import os
import sys
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Optional, Any

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.livechart_client import LiveChartClient
from backend.title_matcher import TitleMatcher
from turkanime_api.anilist_client import anilist_client

app = FastAPI(title="TurkAnime API", version="1.0.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins since we're serving the frontend too
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize clients
livechart = LiveChartClient()

# Pydantic models
class AnimeProgress(BaseModel):
    media_id: int
    progress: int
    status: Optional[str] = None


class TitleMatchRequest(BaseModel):
    query: str
    candidates: List[str]


class TitleNormalizeRequest(BaseModel):
    title: str


class SearchRequest(BaseModel):
    query: str
    source: Optional[str] = "all"  # "all", "livechart", "anilist"


@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "ok", "message": "TurkAnime Backend API", "version": "1.0.0"}


@app.get("/api/anime/current-season")
async def get_current_season():
    """Get current season anime from LiveChart.me"""
    try:
        data = livechart.get_current_season()
        return {"success": True, "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/anime/news")
async def get_news():
    """Get anime news"""
    try:
        news = livechart.get_news()
        return {"success": True, "data": news}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/anime/recently-aired")
async def get_recently_aired():
    """Get recently aired episodes"""
    try:
        episodes = livechart.get_recently_aired()
        return {"success": True, "data": episodes}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/anime/search")
async def search_anime(
    q: str = Query(..., description="Search query"),
    source: str = Query("livechart", description="Search source: livechart or anilist")
):
    """Search for anime"""
    try:
        if source == "livechart":
            results = livechart.search_anime(q)
        elif source == "anilist":
            results = anilist_client.search_anime(q)
        else:
            # Search both sources
            livechart_results = livechart.search_anime(q)
            anilist_results = anilist_client.search_anime(q)
            results = {
                "livechart": livechart_results,
                "anilist": anilist_results
            }
        
        return {"success": True, "data": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/anime/details/{anime_id}")
async def get_anime_details(anime_id: str):
    """Get detailed anime information"""
    try:
        details = livechart.get_anime_details(anime_id)
        if not details:
            raise HTTPException(status_code=404, detail="Anime not found")
        return {"success": True, "data": details}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/titles/match")
async def match_titles(request: TitleMatchRequest):
    """Find best matching title from candidates"""
    try:
        result = TitleMatcher.find_best_match(request.query, request.candidates)
        if result:
            match, score = result
            return {"success": True, "data": {"match": match, "score": score}}
        else:
            return {"success": False, "message": "No good match found"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/titles/normalize")
async def normalize_title(request: TitleNormalizeRequest):
    """Normalize a title for comparison"""
    try:
        normalized = TitleMatcher.normalize_title(request.title)
        return {"success": True, "data": {"normalized": normalized}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# AniList integration endpoints
@app.get("/api/anilist/user")
async def get_anilist_user():
    """Get current AniList user info"""
    try:
        if not anilist_client.access_token:
            raise HTTPException(status_code=401, detail="Not authenticated with AniList")
        
        user = anilist_client.get_current_user()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        return {"success": True, "data": user}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/anilist/list")
async def get_anilist_list(
    status: Optional[str] = Query(None, description="Filter by status: CURRENT, COMPLETED, etc.")
):
    """Get user's AniList anime list"""
    try:
        if not anilist_client.access_token:
            raise HTTPException(status_code=401, detail="Not authenticated with AniList")
        
        user = anilist_client.get_current_user()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        anime_list = anilist_client.get_user_anime_list(user['id'], status)
        return {"success": True, "data": anime_list}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/anilist/progress")
async def update_anilist_progress(progress: AnimeProgress):
    """Update anime progress on AniList"""
    try:
        if not anilist_client.access_token:
            raise HTTPException(status_code=401, detail="Not authenticated with AniList")
        
        success = anilist_client.update_anime_progress(
            progress.media_id,
            progress.progress,
            progress.status
        )
        
        if success:
            return {"success": True, "message": "Progress updated"}
        else:
            raise HTTPException(status_code=400, detail="Failed to update progress")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/anilist/auth/url")
async def get_anilist_auth_url():
    """Get AniList OAuth URL"""
    try:
        auth_url = anilist_client.get_auth_url(response_type="code")
        return {"success": True, "data": {"auth_url": auth_url}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/anilist/auth/token")
async def exchange_anilist_token(code: str):
    """Exchange OAuth code for access token"""
    try:
        success = anilist_client.exchange_code_for_token(code)
        if success:
            user = anilist_client.get_current_user()
            return {"success": True, "data": {"user": user}}
        else:
            raise HTTPException(status_code=400, detail="Failed to exchange token")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Determine frontend path (for development and production)
def get_frontend_path():
    """Get the path to the frontend build directory"""
    # When bundled with PyInstaller
    if getattr(sys, 'frozen', False):
        base_path = Path(sys._MEIPASS)
    else:
        base_path = Path(__file__).parent.parent
    
    frontend_path = base_path / "frontend_build"
    
    if not frontend_path.exists():
        # Fallback to development path
        frontend_path = base_path / "frontend" / "out"
    
    return frontend_path


# Mount static files and serve frontend
frontend_path = get_frontend_path()

if frontend_path.exists():
    # Mount static files
    app.mount("/_next", StaticFiles(directory=str(frontend_path / "_next")), name="next_static")
    
    # Serve index.html for the root and catch-all routes
    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        """Serve the frontend application"""
        # API routes are already handled above
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API endpoint not found")
        
        # Check if it's a static file
        file_path = frontend_path / full_path
        if file_path.is_file():
            return FileResponse(file_path)
        
        # Otherwise serve index.html (SPA routing)
        index_path = frontend_path / "index.html"
        if index_path.exists():
            return FileResponse(index_path)
        
        raise HTTPException(status_code=404, detail="Page not found")
else:
    @app.get("/")
    async def root_fallback():
        return {
            "message": "TurkAnime Backend API",
            "version": "1.0.0",
            "note": "Frontend not available. API endpoints are accessible at /api/*"
        }


if __name__ == "__main__":
    import uvicorn
    from backend.config import API_HOST, API_PORT
    
    print(f"Starting TurkAnime Unified Server...")
    print(f"Server will be available at: http://{API_HOST}:{API_PORT}")
    print(f"API Documentation: http://{API_HOST}:{API_PORT}/docs")
    if frontend_path.exists():
        print(f"Frontend available at: http://{API_HOST}:{API_PORT}")
    print(f"Press CTRL+C to stop the server")
    
    uvicorn.run(
        app,
        host=API_HOST,
        port=API_PORT,
        log_level="info"
    )
