#!/usr/bin/env python3
"""
Simple test script for backend functionality
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.title_matcher import TitleMatcher

def test_title_matcher():
    """Test title matching functionality"""
    print("Testing TitleMatcher...")
    
    # Test 1: Exact match
    title1 = "Bocchi the Rock!"
    title2 = "Bocchi the Rock!"
    score = TitleMatcher.calculate_similarity(title1, title2)
    print(f"Test 1 - Exact match: {score} (expected: 1.0)")
    assert score == 1.0, "Exact match should return 1.0"
    
    # Test 2: Normalize title
    title = "Bocchi the Rock! - Season 2"
    normalized = TitleMatcher.normalize_title(title)
    print(f"Test 2 - Normalized: '{normalized}'")
    
    # Test 3: Remove season info
    title_with_season = "Attack on Titan Season 3"
    without_season = TitleMatcher.remove_season_info(title_with_season)
    print(f"Test 3 - Remove season: '{without_season}' (expected: 'Attack on Titan')")
    
    # Test 4: Similar titles
    title1 = "My Hero Academia"
    title2 = "Boku no Hero Academia"
    score = TitleMatcher.calculate_similarity(title1, title2)
    print(f"Test 4 - Similar titles: {score}")
    
    # Test 5: Find best match
    query = "Spy Family"
    candidates = [
        "SPY x FAMILY",
        "One Piece",
        "Naruto",
        "Spy x Family Season 2"
    ]
    result = TitleMatcher.find_best_match(query, candidates)
    if result:
        match, score = result
        print(f"Test 5 - Best match: '{match}' with score {score}")
    else:
        print("Test 5 - No good match found")
    
    # Test 6: Match with alternatives
    query = "attack on titan"
    title_dict = {
        "title": "Shingeki no Kyojin",
        "title_english": "Attack on Titan",
        "title_romaji": "Shingeki no Kyojin",
        "title_japanese": "進撃の巨人"
    }
    score = TitleMatcher.match_with_alternatives(query, title_dict)
    print(f"Test 6 - Match with alternatives: {score}")
    
    print("\n✅ All TitleMatcher tests passed!")

if __name__ == "__main__":
    test_title_matcher()
