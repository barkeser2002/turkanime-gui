"""
Title matching utilities for Japanese anime titles
Handles romaji, English, and Japanese title conversions and matching
"""

import re
import unicodedata
from typing import List, Tuple, Optional


class TitleMatcher:
    """Utility class for matching anime titles across different formats"""
    
    # Common romaji to English word mappings
    ROMAJI_TO_ENGLISH = {
        "no": "of",
        "to": "and",
        "wa": "is",
        "ga": "the",
        "wo": "the",
        "ni": "in",
        "de": "at",
        "kara": "from",
        "made": "until",
        # Anime-specific terms
        "monogatari": "story",
        "shounen": "boy",
        "shoujo": "girl",
        "isekai": "another world",
        "tensei": "reincarnation",
        "mahou": "magic",
        "sekai": "world",
        "bouken": "adventure",
        "gakuen": "school",
        "senki": "war",
        "senso": "war",
        "densetsu": "legend",
        "eiyuu": "hero",
        "yuusha": "hero",
        "akuma": "demon",
        "kami": "god",
        "tatakau": "fight",
        "tatakai": "battle",
    }
    
    # Common English to romaji mappings (reverse)
    ENGLISH_TO_ROMAJI = {v: k for k, v in ROMAJI_TO_ENGLISH.items()}
    
    @staticmethod
    def normalize_title(title: str) -> str:
        """Normalize title for comparison"""
        if not title:
            return ""
        
        # Convert to lowercase
        title = title.lower()
        
        # Remove extra whitespace
        title = " ".join(title.split())
        
        # Remove special characters but keep spaces and alphanumeric
        title = re.sub(r'[^\w\s\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]', '', title)
        
        # Normalize unicode characters
        title = unicodedata.normalize('NFKD', title)
        
        return title.strip()
    
    @staticmethod
    def remove_season_info(title: str) -> str:
        """Remove season information from title"""
        # Remove patterns like "Season 2", "S2", "2nd Season", etc.
        patterns = [
            r'\s+season\s+\d+',
            r'\s+s\d+',
            r'\s+\d+nd\s+season',
            r'\s+\d+rd\s+season',
            r'\s+\d+th\s+season',
            r'\s+part\s+\d+',
            r'\s+cour\s+\d+',
            r'\s+\d+期',
            r'\s+第\d+期',
        ]
        
        result = title
        for pattern in patterns:
            result = re.sub(pattern, '', result, flags=re.IGNORECASE)
        
        return result.strip()
    
    @classmethod
    def calculate_similarity(cls, title1: str, title2: str) -> float:
        """
        Calculate similarity score between two titles (0.0 to 1.0)
        Uses multiple matching strategies
        """
        if not title1 or not title2:
            return 0.0
        
        # Normalize both titles
        norm1 = cls.normalize_title(title1)
        norm2 = cls.normalize_title(title2)
        
        # Exact match
        if norm1 == norm2:
            return 1.0
        
        # Remove season info and compare
        base1 = cls.remove_season_info(norm1)
        base2 = cls.remove_season_info(norm2)
        
        if base1 == base2:
            return 0.95
        
        # Check if one title contains the other
        if base1 in base2 or base2 in base1:
            return 0.85
        
        # Word-based matching
        words1 = set(base1.split())
        words2 = set(base2.split())
        
        if not words1 or not words2:
            return 0.0
        
        # Calculate Jaccard similarity
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        
        if union == 0:
            return 0.0
        
        jaccard = intersection / union
        
        # Bonus for having important words match
        important_match_bonus = 0.0
        for word in words1:
            if len(word) > 4 and word in words2:  # Long words are more significant
                important_match_bonus += 0.1
        
        return min(jaccard + important_match_bonus, 1.0)
    
    @classmethod
    def find_best_match(cls, query: str, candidates: List[str]) -> Optional[Tuple[str, float]]:
        """
        Find the best matching title from a list of candidates
        Returns (best_match, score) or None if no good match found
        """
        if not query or not candidates:
            return None
        
        best_match = None
        best_score = 0.0
        
        for candidate in candidates:
            score = cls.calculate_similarity(query, candidate)
            if score > best_score:
                best_score = score
                best_match = candidate
        
        # Only return if score is above threshold
        if best_score >= 0.6:
            return (best_match, best_score)
        
        return None
    
    @classmethod
    def extract_romaji_and_english(cls, title: str) -> Tuple[str, str]:
        """
        Try to extract romaji and English versions from a title
        Returns (romaji, english)
        """
        # If title contains both Japanese and Latin characters, try to split
        has_japanese = any('\u3040' <= c <= '\u309F' or '\u30A0' <= c <= '\u30FF' or '\u4E00' <= c <= '\u9FFF' for c in title)
        has_latin = any('a' <= c.lower() <= 'z' for c in title)
        
        if has_japanese and has_latin:
            # Try to find a separator
            for sep in [' - ', ' / ', ' (', '（']:
                if sep in title:
                    parts = title.split(sep, 1)
                    # Determine which part is romaji/english
                    part1_has_jp = any('\u3040' <= c <= '\u309F' or '\u30A0' <= c <= '\u30FF' or '\u4E00' <= c <= '\u9FFF' for c in parts[0])
                    if part1_has_jp:
                        japanese = parts[0].strip()
                        english = parts[1].strip().rstrip(')')
                    else:
                        english = parts[0].strip()
                        japanese = parts[1].strip().rstrip(')')
                    return (english, english)  # Returning english as both for now
        
        # If we can't split, return as-is
        return (title, title)
    
    @classmethod
    def match_with_alternatives(cls, query: str, title_dict: dict) -> float:
        """
        Match query against a dictionary containing multiple title variants
        title_dict should have keys like 'title', 'title_english', 'title_romaji', 'title_japanese'
        """
        scores = []
        
        # Check all available title variants
        for key in ['title', 'title_english', 'title_romaji', 'title_japanese', 'native']:
            if key in title_dict and title_dict[key]:
                score = cls.calculate_similarity(query, title_dict[key])
                scores.append(score)
        
        return max(scores) if scores else 0.0


def normalize_anime_title(title: str) -> str:
    """Helper function for backward compatibility"""
    return TitleMatcher.normalize_title(title)


def match_anime_titles(title1: str, title2: str) -> bool:
    """Helper function to check if two titles match (backward compatibility)"""
    return TitleMatcher.calculate_similarity(title1, title2) >= 0.7
