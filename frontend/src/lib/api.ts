/**
 * API client for TurkAnime backend
 */

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export interface Anime {
  title: string;
  title_english?: string;
  title_romaji?: string;
  title_japanese?: string;
  img_url: string;
  link: string;
  description?: string;
  episodes?: string;
  status?: string;
  studio?: string;
  source?: string;
}

export interface AnimeProgress {
  media_id: number;
  progress: number;
  status?: string;
}

export interface AniListUser {
  id: number;
  name: string;
  avatar?: {
    large: string;
  };
  statistics?: {
    anime: {
      count: number;
      meanScore: number;
      minutesWatched: number;
    };
  };
}

class APIClient {
  private baseUrl: string;

  constructor(baseUrl: string = API_BASE_URL) {
    this.baseUrl = baseUrl;
  }

  private async request<T>(endpoint: string, options?: RequestInit): Promise<T> {
    const url = `${this.baseUrl}${endpoint}`;
    const response = await fetch(url, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...options?.headers,
      },
    });

    if (!response.ok) {
      throw new Error(`API request failed: ${response.statusText}`);
    }

    const data = await response.json();
    return data;
  }

  // Anime endpoints
  async getCurrentSeason() {
    return this.request<{ success: boolean; data: { current_season: Anime[] } }>('/api/anime/current-season');
  }

  async getNews() {
    return this.request<{ success: boolean; data: any[] }>('/api/anime/news');
  }

  async getRecentlyAired() {
    return this.request<{ success: boolean; data: any[] }>('/api/anime/recently-aired');
  }

  async searchAnime(query: string, source: string = 'livechart') {
    const params = new URLSearchParams({ q: query, source });
    return this.request<{ success: boolean; data: Anime[] }>(`/api/anime/search?${params}`);
  }

  async getAnimeDetails(animeId: string) {
    return this.request<{ success: boolean; data: Anime }>(`/api/anime/details/${animeId}`);
  }

  // Title matching endpoints
  async matchTitles(query: string, candidates: string[]) {
    return this.request<{ success: boolean; data?: { match: string; score: number } }>('/api/titles/match', {
      method: 'POST',
      body: JSON.stringify({ query, candidates }),
    });
  }

  async normalizeTitle(title: string) {
    return this.request<{ success: boolean; data: { normalized: string } }>('/api/titles/normalize', {
      method: 'POST',
      body: JSON.stringify({ title }),
    });
  }

  // AniList endpoints
  async getAniListUser() {
    return this.request<{ success: boolean; data: AniListUser }>('/api/anilist/user');
  }

  async getAniListList(status?: string) {
    const params = status ? `?status=${status}` : '';
    return this.request<{ success: boolean; data: any[] }>(`/api/anilist/list${params}`);
  }

  async updateAniListProgress(progress: AnimeProgress) {
    return this.request<{ success: boolean; message: string }>('/api/anilist/progress', {
      method: 'POST',
      body: JSON.stringify(progress),
    });
  }

  async getAniListAuthUrl() {
    return this.request<{ success: boolean; data: { auth_url: string } }>('/api/anilist/auth/url');
  }

  async exchangeAniListToken(code: string) {
    return this.request<{ success: boolean; data: { user: AniListUser } }>('/api/anilist/auth/token', {
      method: 'POST',
      body: JSON.stringify({ code }),
    });
  }
}

export const apiClient = new APIClient();
