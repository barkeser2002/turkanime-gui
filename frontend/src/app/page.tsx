'use client';

import { useEffect, useState } from 'react';
import { apiClient, Anime } from '@/lib/api';
import Image from 'next/image';

export default function Home() {
  const [animes, setAnimes] = useState<Anime[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');

  useEffect(() => {
    loadCurrentSeason();
  }, []);

  const loadCurrentSeason = async () => {
    try {
      setLoading(true);
      const response = await apiClient.getCurrentSeason();
      if (response.success) {
        setAnimes(response.data.current_season);
      }
    } catch (err) {
      setError('Failed to load anime list');
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!searchQuery.trim()) {
      loadCurrentSeason();
      return;
    }

    try {
      setLoading(true);
      const response = await apiClient.searchAnime(searchQuery);
      if (response.success) {
        setAnimes(Array.isArray(response.data) ? response.data : []);
      }
    } catch (err) {
      setError('Search failed');
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-900 text-white">
      <header className="bg-gray-800 py-6 px-4 shadow-lg">
        <div className="container mx-auto">
          <h1 className="text-4xl font-bold mb-4">TürkAnime GUI</h1>
          <form onSubmit={handleSearch} className="flex gap-2">
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Anime ara..."
              className="flex-1 px-4 py-2 rounded bg-gray-700 text-white border border-gray-600 focus:outline-none focus:border-blue-500"
            />
            <button
              type="submit"
              className="px-6 py-2 bg-blue-600 hover:bg-blue-700 rounded font-semibold transition-colors"
            >
              Ara
            </button>
          </form>
        </div>
      </header>

      <main className="container mx-auto px-4 py-8">
        {loading && (
          <div className="text-center py-12">
            <div className="inline-block animate-spin rounded-full h-12 w-12 border-b-2 border-white"></div>
            <p className="mt-4">Yükleniyor...</p>
          </div>
        )}

        {error && (
          <div className="bg-red-900/50 border border-red-500 rounded p-4 mb-4">
            <p>{error}</p>
          </div>
        )}

        {!loading && !error && animes.length === 0 && (
          <div className="text-center py-12">
            <p className="text-xl text-gray-400">Anime bulunamadı</p>
          </div>
        )}

        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-6">
          {animes.map((anime, index) => (
            <div
              key={index}
              className="bg-gray-800 rounded-lg overflow-hidden hover:scale-105 transition-transform cursor-pointer"
            >
              {anime.img_url && (
                <div className="relative w-full h-64">
                  <Image
                    src={anime.img_url}
                    alt={anime.title}
                    fill
                    className="object-cover"
                    sizes="(max-width: 768px) 50vw, (max-width: 1200px) 33vw, 20vw"
                  />
                </div>
              )}
              <div className="p-3">
                <h3 className="font-semibold text-sm line-clamp-2" title={anime.title}>
                  {anime.title}
                </h3>
                {anime.title_english && anime.title_english !== anime.title && (
                  <p className="text-xs text-gray-400 mt-1 line-clamp-1">
                    {anime.title_english}
                  </p>
                )}
              </div>
            </div>
          ))}
        </div>
      </main>

      <footer className="bg-gray-800 py-6 mt-12">
        <div className="container mx-auto px-4 text-center text-gray-400">
          <p>TürkAnime GUI - Anime keşif ve takip uygulaması</p>
        </div>
      </footer>
    </div>
  );
}
