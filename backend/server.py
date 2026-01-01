"""
FastAPI Backend for TurkAnime GUI
Provides REST API endpoints for anime data and progress tracking
"""

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Optional, Any
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.livechart_client import LiveChartClient
from backend.title_matcher import TitleMatcher
from turkanime_api.anilist_client import anilist_client

app = FastAPI(title="TurkAnime API", version="1.0.0")

# CORS middleware for Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],  # Next.js default port
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


@app.get("/")
async def root():
    """Root endpoint"""
    return {"message": "TurkAnime Backend API", "version": "1.0.0"}


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


if __name__ == "__main__":
    import uvicorn
    from backend.config import API_HOST, API_PORT
    
    uvicorn.run(app, host=API_HOST, port=API_PORT)
