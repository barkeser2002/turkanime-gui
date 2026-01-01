#!/usr/bin/env python3
"""
Startup script for TurkAnime Backend Server
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if __name__ == "__main__":
    import uvicorn
    from backend.config import API_HOST, API_PORT
    
    print(f"Starting TurkAnime Backend Server...")
    print(f"Server will be available at: http://{API_HOST}:{API_PORT}")
    print(f"API Documentation: http://{API_HOST}:{API_PORT}/docs")
    print(f"Press CTRL+C to stop the server")
    
    # Use reload=False by default to avoid issues with multiprocessing
    # Set reload=True during development if needed
    uvicorn.run(
        "backend.server:app",
        host=API_HOST,
        port=API_PORT,
        reload=False,
        log_level="info"
    )
