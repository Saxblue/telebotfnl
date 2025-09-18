"""
Otomatik Token Güncelleyici
GitHub'daki token değişikliklerini algılayıp bot'taki token'ları otomatik günceller.
"""

import os
import json
import time
from datetime import datetime
from typing import Dict, Any, List
from dotenv import load_dotenv, set_key
from token_watcher import get_token_watcher

class AutoTokenUpdater:
    """GitHub token değişikliklerini otomatik olarak bot'a aktaran sınıf"""
    
    def __init__(self):
        self.is_enabled = False
        self.update_log = []
        self.max_log_entries = 100
        
        # Token mapping: GitHub field → Environment variable
        self.token_mapping = {
            'authToken': 'KPI_API_KEY',
            'hubAccessToken': 'WITHDRAWAL_HUB_ACCESS_TOKEN',
            'connectionToken': 'WITHDRAWAL_CONNECTION_TOKEN',
            'subscriptionToken': 'WITHDRAWAL_SUBSCRIBE_TOKEN'
        }
        
        # Token watcher'a callback ekle
        watcher = get_token_watcher()
        watcher.add_callback('on_token_change', self._on_token_change)
        watcher.add_callback('on_error', self._on_error)
    
    def enable(self):
        """Otomatik güncellemeyi etkinleştir"""
        self.is_enabled = True
        self._log("🟢 Otomatik token güncellemesi etkinleştirildi")
    
    def disable(self):
        """Otomatik güncellemeyi devre dışı bırak"""
        self.is_enabled = False
        self._log("🔴 Otomatik token güncellemesi devre dışı bırakıldı")
    
    def _log(self, message: str, level: str = "info"):
        """Log mesajı ekle"""
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'message': message,
            'level': level
        }
        
        self.update_log.append(log_entry)
        
        # Log boyutunu sınırla
        if len(self.update_log) > self.max_log_entries:
            self.update_log = self.update_log[-self.max_log_entries:]
        
        print(f"[AutoTokenUpdater] {message}")
    
    def _on_token_change(self, new_tokens: Dict, old_tokens: Dict, changes: Dict):
        """Token değişikliği callback'i"""
        if not self.is_enabled:
            self._log("ℹ️ Token değişikliği algılandı ama otomatik güncelleme kapalı", "info")
            return
        
        self._log(f"🔔 Token değişikliği algılandı: {len(changes['changed_tokens'])} token değişti", "info")
        
        # Değişen token'ları güncelle
        success_count = 0
        total_count = 0
        
        for change in changes['changed_tokens']:
            field = change['field']
            new_value = change['new_value']
            
            if field in self.token_mapping:
                env_key = self.token_mapping[field]
                
                # Null veya boş değerleri atla
                if new_value and new_value != 'null' and str(new_value).strip():
                    total_count += 1
                    
                    if self._update_env_variable(env_key, str(new_value)):
                        success_count += 1
                        self._log(f"✅ {field} → {env_key} güncellendi", "success")
                    else:
                        self._log(f"❌ {field} → {env_key} güncellenemedi", "error")
                else:
                    self._log(f"⚠️ {field} boş veya null, atlanıyor", "warning")
        
        if total_count > 0:
            self._log(f"🎉 Otomatik güncelleme tamamlandı: {success_count}/{total_count} token güncellendi", "success")
        else:
            self._log("ℹ️ Güncellenecek geçerli token bulunamadı", "info")
    
    def _on_error(self, error_msg: str, error_count: int):
        """Hata callback'i"""
        self._log(f"❌ Token watcher hatası: {error_msg} (#{error_count})", "error")
    
    def _update_env_variable(self, key: str, value: str) -> bool:
        """Environment variable'ı güncelle"""
        try:
            # Önce mevcut oturumda güncelle
            os.environ[key] = value
            
            # .env dosyasını güvenli şekilde güncelle
            env_content = {}
            
            # Mevcut .env dosyasını oku (varsa)
            if os.path.exists('.env'):
                try:
                    with open('.env', 'r', encoding='utf-8') as f:
                        for line in f:
                            line = line.strip()
                            if line and not line.startswith('#') and '=' in line:
                                k, v = line.split('=', 1)
                                env_content[k.strip()] = v.strip()
                except UnicodeDecodeError:
                    # Encoding hatası varsa boş başla
                    env_content = {}
            
            # Yeni değeri ekle/güncelle
            env_content[key] = value
            
            # Dosyayı yeniden yaz
            with open('.env', 'w', encoding='utf-8') as f:
                f.write('# BetConstruct KPI Bot Environment Variables\n')
                f.write('# Bu dosyayı güvenli bir yerde saklayın\n\n')
                for k, v in env_content.items():
                    f.write(f'{k}={v}\n')
            
            return True
            
        except Exception as e:
            self._log(f"Environment variable güncelleme hatası ({key}): {e}", "error")
            return False
    
    def manual_update(self, github_tokens: Dict[str, Any]) -> Dict[str, Any]:
        """Manuel token güncellemesi"""
        self._log("🔄 Manuel token güncellemesi başlatıldı", "info")
        
        success_count = 0
        total_count = 0
        results = {}
        
        for github_key, env_key in self.token_mapping.items():
            github_value = github_tokens.get(github_key)
            
            if github_value and github_value != 'null' and str(github_value).strip():
                total_count += 1
                
                if self._update_env_variable(env_key, str(github_value)):
                    success_count += 1
                    results[github_key] = {'status': 'success', 'env_key': env_key}
                    self._log(f"✅ Manuel güncelleme: {github_key} → {env_key}", "success")
                else:
                    results[github_key] = {'status': 'error', 'env_key': env_key}
                    self._log(f"❌ Manuel güncelleme hatası: {github_key} → {env_key}", "error")
            else:
                results[github_key] = {'status': 'skipped', 'reason': 'empty_or_null'}
                self._log(f"⚠️ Manuel güncelleme atlandı: {github_key} (boş/null)", "warning")
        
        self._log(f"🎉 Manuel güncelleme tamamlandı: {success_count}/{total_count} token güncellendi", "success")
        
        return {
            'success_count': success_count,
            'total_count': total_count,
            'results': results
        }
    
    def get_status(self) -> Dict[str, Any]:
        """Güncelleyici durumunu döndür"""
        return {
            'is_enabled': self.is_enabled,
            'token_mapping': self.token_mapping,
            'log_count': len(self.update_log),
            'last_log': self.update_log[-1] if self.update_log else None
        }
    
    def get_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Son logları döndür"""
        return self.update_log[-limit:] if self.update_log else []
    
    def clear_logs(self):
        """Logları temizle"""
        self.update_log.clear()
        self._log("🗑️ Loglar temizlendi", "info")
    
    def test_connection(self) -> bool:
        """GitHub bağlantısını test et"""
        try:
            watcher = get_token_watcher()
            result = watcher.fetch_tokens()
            
            if result and result.get('status') == 'success':
                self._log("✅ GitHub bağlantı testi başarılı", "success")
                return True
            else:
                self._log("❌ GitHub bağlantı testi başarısız", "error")
                return False
                
        except Exception as e:
            self._log(f"❌ GitHub bağlantı testi hatası: {e}", "error")
            return False


# Global updater instance
_global_updater = None

def get_auto_updater() -> AutoTokenUpdater:
    """Global auto updater instance'ını döndür"""
    global _global_updater
    if _global_updater is None:
        _global_updater = AutoTokenUpdater()
    return _global_updater

def enable_auto_update():
    """Otomatik güncellemeyi etkinleştir"""
    updater = get_auto_updater()
    updater.enable()

def disable_auto_update():
    """Otomatik güncellemeyi devre dışı bırak"""
    updater = get_auto_updater()
    updater.disable()

def manual_token_update(github_tokens: Dict[str, Any]) -> Dict[str, Any]:
    """Manuel token güncellemesi"""
    updater = get_auto_updater()
    return updater.manual_update(github_tokens)

def get_updater_status() -> Dict[str, Any]:
    """Güncelleyici durumunu döndür"""
    updater = get_auto_updater()
    return updater.get_status()

def get_update_logs(limit: int = 50) -> List[Dict[str, Any]]:
    """Güncelleme loglarını döndür"""
    updater = get_auto_updater()
    return updater.get_logs(limit)

def clear_update_logs():
    """Güncelleme loglarını temizle"""
    updater = get_auto_updater()
    updater.clear_logs()

def test_github_connection() -> bool:
    """GitHub bağlantısını test et"""
    updater = get_auto_updater()
    return updater.test_connection()


# Test fonksiyonu
if __name__ == "__main__":
    # Test
    updater = AutoTokenUpdater()
    updater.enable()
    
    # Test token'ları
    test_tokens = {
        'authToken': 'test_auth_token_123',
        'hubAccessToken': 'test_hub_token_456',
        'connectionToken': 'test_conn_token_789',
        'subscriptionToken': 'test_sub_token_000'
    }
    
    print("Manuel güncelleme testi...")
    result = updater.manual_update(test_tokens)
    print(f"Sonuç: {result}")
    
    print("\nDurum:")
    status = updater.get_status()
    print(f"Durum: {status}")
    
    print("\nLoglar:")
    logs = updater.get_logs(10)
    for log in logs:
        print(f"[{log['timestamp']}] {log['message']}")
