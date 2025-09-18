"""
GitHub Token Watcher - Canlı Token Güncellemesi
Bu modül GitHub'daki tokens.json dosyasını sürekli izler ve değişiklikleri algılar.
"""

import requests
import json
import time
import threading
import os
from datetime import datetime
from typing import Dict, Any, Optional, Callable
import hashlib

class GitHubTokenWatcher:
    """GitHub'daki tokens.json dosyasını izleyen sınıf"""
    
    def __init__(self, 
                 github_url: str = "https://raw.githubusercontent.com/Saxblue/telebotfnl/refs/heads/main/tokens.json",
                 check_interval: int = 30,
                 on_token_change: Optional[Callable] = None):
        """
        Args:
            github_url: GitHub raw URL
            check_interval: Kontrol aralığı (saniye)
            on_token_change: Token değiştiğinde çağrılacak callback fonksiyonu
        """
        self.github_url = github_url
        self.check_interval = check_interval
        self.on_token_change = on_token_change
        
        self.is_running = False
        self.thread = None
        self.last_hash = None
        self.last_tokens = {}
        self.last_check_time = None
        self.error_count = 0
        self.max_errors = 5
        
        # Callback fonksiyonları
        self.callbacks = {
            'on_token_change': [],
            'on_error': [],
            'on_status_change': []
        }
    
    def add_callback(self, event_type: str, callback: Callable):
        """Callback fonksiyonu ekle"""
        if event_type in self.callbacks:
            self.callbacks[event_type].append(callback)
    
    def remove_callback(self, event_type: str, callback: Callable):
        """Callback fonksiyonu kaldır"""
        if event_type in self.callbacks and callback in self.callbacks[event_type]:
            self.callbacks[event_type].remove(callback)
    
    def _trigger_callback(self, event_type: str, *args, **kwargs):
        """Callback fonksiyonlarını tetikle"""
        for callback in self.callbacks.get(event_type, []):
            try:
                callback(*args, **kwargs)
            except Exception as e:
                print(f"Callback hatası ({event_type}): {e}")
    
    def fetch_tokens(self) -> Optional[Dict[str, Any]]:
        """GitHub'dan token'ları çek"""
        try:
            response = requests.get(self.github_url, timeout=10)
            response.raise_for_status()
            
            # JSON parse et
            tokens_data = response.json()
            
            # Hash hesapla (değişiklik kontrolü için)
            content_hash = hashlib.md5(response.text.encode()).hexdigest()
            
            self.error_count = 0  # Başarılı istek, hata sayacını sıfırla
            return {
                'data': tokens_data,
                'hash': content_hash,
                'timestamp': datetime.now().isoformat(),
                'status': 'success'
            }
            
        except requests.exceptions.RequestException as e:
            self.error_count += 1
            error_msg = f"GitHub API hatası: {e}"
            self._trigger_callback('on_error', error_msg, self.error_count)
            return {
                'data': None,
                'hash': None,
                'timestamp': datetime.now().isoformat(),
                'status': 'error',
                'error': error_msg
            }
        except json.JSONDecodeError as e:
            self.error_count += 1
            error_msg = f"JSON parse hatası: {e}"
            self._trigger_callback('on_error', error_msg, self.error_count)
            return {
                'data': None,
                'hash': None,
                'timestamp': datetime.now().isoformat(),
                'status': 'error',
                'error': error_msg
            }
    
    def check_for_changes(self) -> bool:
        """Token değişikliklerini kontrol et"""
        result = self.fetch_tokens()
        self.last_check_time = datetime.now()
        
        if result['status'] == 'error':
            return False
        
        current_hash = result['hash']
        current_tokens = result['data']
        
        # İlk çalıştırma
        if self.last_hash is None:
            self.last_hash = current_hash
            self.last_tokens = current_tokens
            print(f"🔄 Token watcher başlatıldı - İlk token'lar yüklendi")
            return False
        
        # Hash değişikliği kontrolü
        if current_hash != self.last_hash:
            print(f"🔔 Token değişikliği algılandı! {datetime.now().strftime('%H:%M:%S')}")
            
            # Değişiklikleri analiz et
            changes = self._analyze_changes(self.last_tokens, current_tokens)
            
            # Callback'leri tetikle
            self._trigger_callback('on_token_change', current_tokens, self.last_tokens, changes)
            
            # Güncelle
            self.last_hash = current_hash
            self.last_tokens = current_tokens
            
            return True
        
        return False
    
    def _analyze_changes(self, old_tokens: Dict, new_tokens: Dict) -> Dict[str, Any]:
        """Token değişikliklerini analiz et"""
        changes = {
            'changed_tokens': [],
            'new_tokens': [],
            'removed_tokens': [],
            'timestamp': datetime.now().isoformat()
        }
        
        # Ana token'ları kontrol et
        token_fields = ['authToken', 'hubAccessToken', 'connectionToken', 'subscriptionToken']
        
        for field in token_fields:
            old_value = old_tokens.get(field)
            new_value = new_tokens.get(field)
            
            if old_value != new_value:
                changes['changed_tokens'].append({
                    'field': field,
                    'old_value': old_value,
                    'new_value': new_value,
                    'changed_at': new_tokens.get('lastUpdated', datetime.now().isoformat())
                })
        
        return changes
    
    def _watch_loop(self):
        """Ana izleme döngüsü"""
        print(f"🚀 GitHub Token Watcher başlatıldı - {self.check_interval}s aralıklarla kontrol")
        
        while self.is_running:
            try:
                self.check_for_changes()
                
                # Çok fazla hata varsa duraksama
                if self.error_count >= self.max_errors:
                    print(f"⚠️ Çok fazla hata ({self.error_count}), 5 dakika bekleniyor...")
                    time.sleep(300)  # 5 dakika bekle
                    self.error_count = 0  # Hata sayacını sıfırla
                
                # Normal bekleme
                time.sleep(self.check_interval)
                
            except Exception as e:
                print(f"❌ Watcher döngü hatası: {e}")
                time.sleep(60)  # Hata durumunda 1 dakika bekle
    
    def start(self):
        """Token izlemeyi başlat"""
        if self.is_running:
            print("⚠️ Token watcher zaten çalışıyor")
            return False
        
        self.is_running = True
        self.thread = threading.Thread(target=self._watch_loop, daemon=True)
        self.thread.start()
        
        self._trigger_callback('on_status_change', 'started')
        print("✅ GitHub Token Watcher başlatıldı")
        return True
    
    def stop(self):
        """Token izlemeyi durdur"""
        if not self.is_running:
            print("⚠️ Token watcher zaten durmuş")
            return False
        
        self.is_running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
        
        self._trigger_callback('on_status_change', 'stopped')
        print("🛑 GitHub Token Watcher durduruldu")
        return True
    
    def get_status(self) -> Dict[str, Any]:
        """Watcher durumunu döndür"""
        return {
            'is_running': self.is_running,
            'last_check_time': self.last_check_time.isoformat() if self.last_check_time else None,
            'error_count': self.error_count,
            'check_interval': self.check_interval,
            'github_url': self.github_url,
            'last_tokens': self.last_tokens,
            'thread_alive': self.thread.is_alive() if self.thread else False
        }
    
    def get_current_tokens(self) -> Dict[str, Any]:
        """Mevcut token'ları döndür"""
        return self.last_tokens.copy()
    
    def force_check(self) -> bool:
        """Zorla kontrol et"""
        print("🔄 Zorla token kontrolü yapılıyor...")
        return self.check_for_changes()


# Global watcher instance
_global_watcher = None

def get_token_watcher() -> GitHubTokenWatcher:
    """Global token watcher instance'ını döndür"""
    global _global_watcher
    if _global_watcher is None:
        _global_watcher = GitHubTokenWatcher()
    return _global_watcher

def start_token_watcher(check_interval: int = 30) -> bool:
    """Token watcher'ı başlat"""
    watcher = get_token_watcher()
    watcher.check_interval = check_interval
    return watcher.start()

def stop_token_watcher() -> bool:
    """Token watcher'ı durdur"""
    watcher = get_token_watcher()
    return watcher.stop()

def get_watcher_status() -> Dict[str, Any]:
    """Watcher durumunu döndür"""
    watcher = get_token_watcher()
    return watcher.get_status()

def get_current_tokens() -> Dict[str, Any]:
    """Mevcut token'ları döndür"""
    watcher = get_token_watcher()
    return watcher.get_current_tokens()

def force_token_check() -> bool:
    """Zorla token kontrolü yap"""
    watcher = get_token_watcher()
    return watcher.force_check()


# Test fonksiyonu
if __name__ == "__main__":
    def on_token_change(new_tokens, old_tokens, changes):
        print(f"🔔 Token değişti!")
        print(f"Değişen token'lar: {len(changes['changed_tokens'])}")
        for change in changes['changed_tokens']:
            print(f"  - {change['field']}: {change['old_value'][:20]}... → {change['new_value'][:20]}...")
    
    def on_error(error_msg, error_count):
        print(f"❌ Hata: {error_msg} (#{error_count})")
    
    def on_status_change(status):
        print(f"📊 Durum değişti: {status}")
    
    # Test
    watcher = GitHubTokenWatcher(check_interval=10)
    watcher.add_callback('on_token_change', on_token_change)
    watcher.add_callback('on_error', on_error)
    watcher.add_callback('on_status_change', on_status_change)
    
    watcher.start()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Durdurma komutu alındı...")
        watcher.stop()
