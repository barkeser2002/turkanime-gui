"""
LiveChart.me client for fetching current season anime
"""

import requests
import feedparser
from bs4 import BeautifulSoup
from typing import List, Dict, Optional
import re
from datetime import datetime
from backend import config


class LiveChartClient:
    """Client for LiveChart.me API"""
    
    def __init__(self, timeout: int = config.TIMEOUT):
        self.timeout = timeout
        self.base_url = config.LIVECHART_BASE_URL
        self.headlines_feed = config.LIVECHART_HEADLINES_FEED
        self.episodes_feed = config.LIVECHART_EPISODES_FEED
        self.search_endpoint = config.LIVECHART_SEARCH_ENDPOINT
        
    def _parse_url(self, url: str) -> BeautifulSoup:
        """Parse URL and return BeautifulSoup object"""
        response = requests.get(url, timeout=self.timeout)
        response.raise_for_status()
        return BeautifulSoup(response.text, "html.parser")
    
    def _parse_feed(self, url: str) -> List[Dict]:
        """Parse RSS feed and return entries"""
        feed = feedparser.parse(url)
        return feed.entries
    
    def get_current_season(self) -> Dict[str, List[Dict]]:
        """Get current season anime from LiveChart.me"""
        url = f"{self.base_url}/timetable"
        soup = self._parse_url(url)
        
        animes = []
        
        # Find anime cards on the page
        for card in soup.find_all("div", class_=re.compile(r"anime-card")):
            try:
                title_elem = card.find("h3") or card.find("h2")
                if not title_elem:
                    continue
                    
                title = title_elem.text.strip()
                
                # Extract link
                link_elem = card.find("a", href=True)
                link = f"{self.base_url}{link_elem['href']}" if link_elem else ""
                
                # Extract image
                img_elem = card.find("img")
                img_url = img_elem.get("src") or img_elem.get("data-src", "") if img_elem else ""
                if img_url and not img_url.startswith("http"):
                    img_url = f"{self.base_url}{img_url}"
                
                # Extract other details
                anime_info = {
                    "title": title,
                    "title_english": title,  # Will be improved with better parsing
                    "title_romaji": "",
                    "img_url": img_url,
                    "link": link,
                    "source": "livechart"
                }
                
                animes.append(anime_info)
            except Exception as e:
                print(f"Error parsing anime card: {e}")
                continue
        
        return {"current_season": animes}
    
    def get_anime_details(self, anime_id: str) -> Optional[Dict]:
        """Get detailed anime information from LiveChart.me"""
        url = f"{self.base_url}/anime/{anime_id}"
        try:
            soup = self._parse_url(url)
            
            # Extract title
            title_elem = soup.find("h1", class_=re.compile(r"anime-title"))
            title = title_elem.text.strip() if title_elem else ""
            
            # Extract alternative titles
            title_english = title
            title_romaji = ""
            title_japanese = ""
            
            alt_titles = soup.find_all("div", class_=re.compile(r"alt-title"))
            for alt in alt_titles:
                text = alt.text.strip()
                # Try to detect Japanese characters
                if any('\u3040' <= c <= '\u309F' or '\u30A0' <= c <= '\u30FF' or '\u4E00' <= c <= '\u9FFF' for c in text):
                    title_japanese = text
                else:
                    title_romaji = text
            
            # Extract image
            img_elem = soup.find("img", class_=re.compile(r"anime-poster"))
            img_url = img_elem.get("src", "") if img_elem else ""
            
            # Extract description
            desc_elem = soup.find("div", class_=re.compile(r"anime-description"))
            description = desc_elem.text.strip() if desc_elem else ""
            
            # Extract metadata
            metadata = {}
            info_items = soup.find_all("div", class_=re.compile(r"info-item"))
            for item in info_items:
                label_elem = item.find("span", class_=re.compile(r"label"))
                value_elem = item.find("span", class_=re.compile(r"value"))
                if label_elem and value_elem:
                    metadata[label_elem.text.strip()] = value_elem.text.strip()
            
            return {
                "title": title,
                "title_english": title_english,
                "title_romaji": title_romaji,
                "title_japanese": title_japanese,
                "img_url": img_url,
                "description": description,
                "episodes": metadata.get("Episodes", "Unknown"),
                "status": metadata.get("Status", "Unknown"),
                "studio": metadata.get("Studio", "Unknown"),
                "source": "livechart",
                "link": url
            }
        except Exception as e:
            print(f"Error fetching anime details: {e}")
            return None
    
    def search_anime(self, query: str) -> List[Dict]:
        """Search for anime on LiveChart.me"""
        url = f"{self.search_endpoint}?q={query}"
        try:
            soup = self._parse_url(url)
            results = []
            
            # Find search result cards
            for card in soup.find_all("div", class_=re.compile(r"anime-card")):
                try:
                    title_elem = card.find("h3") or card.find("h2")
                    if not title_elem:
                        continue
                    
                    title = title_elem.text.strip()
                    
                    link_elem = card.find("a", href=True)
                    link = f"{self.base_url}{link_elem['href']}" if link_elem else ""
                    
                    img_elem = card.find("img")
                    img_url = img_elem.get("src") or img_elem.get("data-src", "") if img_elem else ""
                    
                    results.append({
                        "title": title,
                        "link": link,
                        "img_url": img_url,
                        "source": "livechart"
                    })
                except Exception:
                    continue
            
            return results[:10]  # Return top 10 results
        except Exception as e:
            print(f"Error searching anime: {e}")
            return []
    
    def get_news(self) -> List[Dict]:
        """Get anime news from LiveChart.me feed"""
        try:
            entries = self._parse_feed(self.headlines_feed)
            return [
                {
                    "title": entry.get("title", ""),
                    "link": entry.get("link", ""),
                    "published": entry.get("published", ""),
                    "summary": entry.get("summary", "")
                }
                for entry in entries
            ]
        except Exception as e:
            print(f"Error fetching news: {e}")
            return []
    
    def get_recently_aired(self) -> List[Dict]:
        """Get recently aired episodes from LiveChart.me feed"""
        try:
            entries = self._parse_feed(self.episodes_feed)
            return [
                {
                    "title": entry.get("title", ""),
                    "link": entry.get("link", ""),
                    "published": entry.get("published", ""),
                    "summary": entry.get("summary", "")
                }
                for entry in entries
            ]
        except Exception as e:
            print(f"Error fetching recently aired: {e}")
            return []
