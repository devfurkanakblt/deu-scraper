#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DEÜ Web Scraper
Bu script belirtilen DEÜ sayfalarından rel='bookmark' olan a etiketlerindeki linkleri çeker.
"""

import requests
from bs4 import BeautifulSoup
import time
import json
from urllib.parse import urljoin, urlparse
import sys
import os
from dotenv import load_dotenv
import psycopg2
import psycopg2.extras

class DEUScraper:
    def __init__(self):
        # .env dosyasını yükle
        load_dotenv()
        
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })
        
        # Pushbullet API anahtarı
        self.pushbullet_api_key = os.getenv('PUSHBULLET_API_KEY')
        if not self.pushbullet_api_key:
            print("⚠️  UYARI: PUSHBULLET_API_KEY .env dosyasında bulunamadı!")
            print("   Pushbullet bildirimleri gönderilmeyecek.")
            print("   .env dosyası oluşturun ve PUSHBULLET_API_KEY=your_key_here ekleyin")

        # PostgreSQL bağlantısı
        self.database_url = os.getenv('DATABASE_URL')
        self.db_conn = None
        if not self.database_url:
            print("⚠️  UYARI: DATABASE_URL bulunamadı. Veriler dosyaya kaydedilmeyecek.")
        else:
            self._init_db()

    def _init_db(self):
        """
        PostgreSQL'e bağlanır ve gerekli tabloyu oluşturur.
        """
        try:
            # Heroku için sslmode=require genelde gerekli
            if 'sslmode=' not in self.database_url:
                conn_str = self.database_url + ("?sslmode=require" if '?' not in self.database_url else "&sslmode=require")
            else:
                conn_str = self.database_url
            self.db_conn = psycopg2.connect(conn_str)
            self.db_conn.autocommit = True
            self._ensure_schema()
            print("🗄️  PostgreSQL bağlantısı kuruldu ve şema doğrulandı.")
        except Exception as e:
            self.db_conn = None
            print(f"❌ PostgreSQL bağlantısı kurulamadı: {e}")

    def _ensure_schema(self):
        """
        bookmarks tablosu yoksa oluşturur.
        """
        with self.db_conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS bookmarks (
                    url TEXT PRIMARY KEY,
                    text TEXT NOT NULL,
                    title TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                );
                """
            )
        
    def get_page_content(self, url):
        """
        Verilen URL'den sayfa içeriğini çeker
        """
        try:
            print(f"Sayfa yükleniyor: {url}")
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            response.encoding = 'utf-8'
            return response.text
        except requests.exceptions.RequestException as e:
            print(f"Hata: {url} yüklenirken hata oluştu: {e}")
            return None
    
    def extract_bookmark_links(self, html_content, base_url, limit=5):
        """
        HTML içeriğinden rel='bookmark' olan a etiketlerindeki linkleri çıkarır
        Sadece ilk 'limit' kadar link alır (varsayılan: 5)
        """
        if not html_content:
            return []
            
        soup = BeautifulSoup(html_content, 'html.parser')
        bookmark_links = []
        
        # rel='bookmark' olan a etiketlerini bul
        bookmark_elements = soup.find_all('a', rel='bookmark')
        
        # Sadece ilk 'limit' kadar link al
        for element in bookmark_elements[:limit]:
            href = element.get('href')
            if href:
                # Göreceli URL'leri mutlak URL'ye çevir
                absolute_url = urljoin(base_url, href)
                link_text = element.get_text(strip=True)
                
                bookmark_links.append({
                    'url': absolute_url,
                    'text': link_text,
                    'title': element.get('title', ''),
                    'base_url': base_url
                })
        
        return bookmark_links
    
    def scrape_url(self, url):
        """
        Tek bir URL'yi scrape eder
        """
        print(f"\n{'='*60}")
        print(f"Scraping: {url}")
        print(f"{'='*60}")
        
        html_content = self.get_page_content(url)
        if html_content:
            bookmark_links = self.extract_bookmark_links(html_content, url)
            print(f"Bulunan bookmark link sayısı: {len(bookmark_links)}")
            
            for i, link in enumerate(bookmark_links, 1):
                print(f"{i:2d}. {link['text']}")
                print(f"    URL: {link['url']}")
                if link['title']:
                    print(f"    Title: {link['title']}")
                print()
            
            return bookmark_links
        else:
            print(f"Sayfa yüklenemedi: {url}")
            return []
    
    def scrape_all_urls(self, urls):
        """
        Tüm URL'leri scrape eder
        """
        all_links = []
        
        for url in urls:
            links = self.scrape_url(url)
            all_links.extend(links)
            
            # Rate limiting - sayfalar arası bekleme
            time.sleep(2)
        
        return all_links
    
    def load_existing_bookmarks(self, filename='deu_bookmark_links.json'):
        """
        Mevcut bookmarkları veri tabanından yükler.
        DATABASE_URL yoksa eski JSON dosyasından okur (geriye dönük).
        """
        if self.db_conn:
            try:
                with self.db_conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                    cur.execute("SELECT url, text, title, base_url FROM bookmarks")
                    rows = cur.fetchall()
                    return [{
                        'url': row['url'],
                        'text': row['text'],
                        'title': row['title'] or '',
                        'base_url': row['base_url']
                    } for row in rows]
            except Exception as e:
                print(f"❌ Veri tabanından veriler alınamadı: {e}")
                return []
        # Geriye dönük: Dosyadan oku
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            return []
        except Exception as e:
            print(f"Mevcut dosya okunurken hata oluştu: {e}")
            return []
    
    def find_new_bookmarks(self, new_links, existing_links):
        """
        Yeni linkler ile mevcut linkleri karşılaştırır ve sadece yeni olanları döndürür
        """
        existing_urls = {link['url'] for link in existing_links}
        new_bookmarks = []
        
        for link in new_links:
            if link['url'] not in existing_urls:
                new_bookmarks.append(link)
        
        return new_bookmarks
    
    def send_pushbullet_notification(self, title, body, url=None):
        """
        Pushbullet API kullanarak bildirim gönderir
        """
        if not self.pushbullet_api_key:
            print(f"📱 Pushbullet bildirimi gönderilemedi (API anahtarı yok): {title}")
            return False
        
        try:
            pushbullet_url = "https://api.pushbullet.com/v2/pushes"
            headers = {
                'Access-Token': self.pushbullet_api_key,
                'Content-Type': 'application/json'
            }
            
            data = {
                'type': 'note',
                'title': title,
                'body': body
            }
            
            # URL varsa link olarak ekle
            if url:
                data['type'] = 'link'
                data['url'] = url
            
            response = requests.post(pushbullet_url, headers=headers, json=data)
            response.raise_for_status()
            
            print(f"📱 Pushbullet bildirimi gönderildi: {title}")
            return True
            
        except requests.exceptions.RequestException as e:
            print(f"❌ Pushbullet bildirimi gönderilemedi: {e}")
            return False
        except Exception as e:
            print(f"❌ Pushbullet hatası: {e}")
            return False
    
    def send_new_bookmark_notifications(self, new_bookmarks):
        """
        Yeni bookmarklar için Pushbullet bildirimleri gönderir
        """
        if not new_bookmarks:
            return
        
        print(f"\n📱 {len(new_bookmarks)} yeni bookmark için Pushbullet bildirimi gönderiliyor...")
        
        for bookmark in new_bookmarks:
            title = "🔖 Yeni DEÜ Duyurusu"
            body = f"{bookmark['text']}\n\nKaynak: {bookmark['base_url']}"
            url = bookmark['url']
            
            self.send_pushbullet_notification(title, body, url)
            
            # Rate limiting - API limitlerini aşmamak için
            time.sleep(1)
    
    def save_results(self, results, filename='deu_bookmark_links.json'):
        """
        Artık JSON'a değil, DB'ye yazıyoruz. Bu metod geriye dönük uyumluluk için tutuldu.
        """
        if not self.db_conn:
            # DATABASE_URL yoksa dosyaya yazmaya devam et
            try:
                with open(filename, 'w', encoding='utf-8') as f:
                    json.dump(results, f, ensure_ascii=False, indent=2)
                print(f"\nSonuçlar {filename} dosyasına kaydedildi.")
            except Exception as e:
                print(f"Sonuçlar kaydedilirken hata oluştu: {e}")
            return
        # DB'ye toplu insert (mevcutları atla)
        self.append_new_bookmarks(results)
    
    def append_new_bookmarks(self, new_bookmarks, filename='deu_bookmark_links.json'):
        """
        Yeni bookmarkları veri tabanına ekler (url üzerinde unique). DATABASE_URL yoksa dosyaya yazar.
        """
        if self.db_conn:
            try:
                with self.db_conn.cursor() as cur:
                    cur.executemany(
                        """
                        INSERT INTO bookmarks (url, text, title, base_url)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (url) DO NOTHING
                        """,
                        [(b['url'], b['text'], b.get('title', ''), b['base_url']) for b in new_bookmarks]
                    )
                print(f"\n{len(new_bookmarks)} yeni bookmark veri tabanına eklendi (mevcut olanlar atlandı).")
            except Exception as e:
                print(f"Yeni bookmarklar DB'ye eklenirken hata oluştu: {e}")
            return
        # Geriye dönük: Dosyaya yaz
        try:
            existing_bookmarks = self.load_existing_bookmarks(filename)
            all_bookmarks = existing_bookmarks + new_bookmarks
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(all_bookmarks, f, ensure_ascii=False, indent=2)
            print(f"\n{len(new_bookmarks)} yeni bookmark {filename} dosyasına eklendi.")
            print(f"Toplam bookmark sayısı: {len(all_bookmarks)}")
        except Exception as e:
            print(f"Yeni bookmarklar eklenirken hata oluştu: {e}")
    
    def print_summary(self, results):
        """
        Sonuçların özetini yazdırır
        """
        print(f"\n{'='*60}")
        print("ÖZET")
        print(f"{'='*60}")
        print(f"Toplam bookmark link sayısı: {len(results)}")
        
        # URL'lere göre grupla
        url_groups = {}
        for link in results:
            base_url = link['base_url']
            if base_url not in url_groups:
                url_groups[base_url] = []
            url_groups[base_url].append(link)
        
        for base_url, links in url_groups.items():
            print(f"\n{base_url}: {len(links)} link")
            for link in links[:3]:  # İlk 3 linki göster
                print(f"  - {link['text']}")
            if len(links) > 3:
                print(f"  ... ve {len(links) - 3} link daha")

def main():
    """
    Ana fonksiyon
    """
    # Scrape edilecek URL'ler
    urls = [
        "https://www.deu.edu.tr/tum-duyurular/",
        "https://csc.deu.edu.tr/tr/",
        "https://fen.deu.edu.tr/tr/"
    ]
    
    print("DEÜ Web Scraper Başlatılıyor...")
    print("Bu script belirtilen sayfalardan rel='bookmark' olan linkleri çekecek.")
    print("Her sayfadan sadece ilk 5 link alınacak ve sadece yeni bookmarklar eklenecek.")
    
    scraper = DEUScraper()
    
    try:
        # Mevcut bookmarkları yükle
        existing_bookmarks = scraper.load_existing_bookmarks()
        print(f"\nMevcut bookmark sayısı: {len(existing_bookmarks)}")
        
        # Tüm URL'leri scrape et (her sayfadan sadece 5 link)
        new_results = scraper.scrape_all_urls(urls)
        
        # Yeni bookmarkları bul
        new_bookmarks = scraper.find_new_bookmarks(new_results, existing_bookmarks)
        
        print(f"\nBu kontrolde bulunan toplam link: {len(new_results)}")
        print(f"Yeni bookmark sayısı: {len(new_bookmarks)}")
        
        if new_bookmarks:
            # Sadece yeni bookmarkları ekle
            scraper.append_new_bookmarks(new_bookmarks)
            
            # Pushbullet bildirimleri gönder
            scraper.send_new_bookmark_notifications(new_bookmarks)
            
            # Yeni bookmarkları göster
            print(f"\n{'='*60}")
            print("YENİ BULUNAN BOOKMARKLAR")
            print(f"{'='*60}")
            for i, bookmark in enumerate(new_bookmarks, 1):
                print(f"{i:2d}. {bookmark['text']}")
                print(f"    URL: {bookmark['url']}")
                print(f"    Kaynak: {bookmark['base_url']}")
                print()
        else:
            print("\nYeni bookmark bulunamadı. Tüm linkler zaten mevcut.")
        
        # Güncellenmiş özet
        updated_bookmarks = scraper.load_existing_bookmarks()
        scraper.print_summary(updated_bookmarks)
        
        print(f"\nScraping tamamlandı! Toplam {len(updated_bookmarks)} bookmark mevcut.")
        
    except KeyboardInterrupt:
        print("\nScraping kullanıcı tarafından durduruldu.")
    except Exception as e:
        print(f"\nBeklenmeyen hata: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
