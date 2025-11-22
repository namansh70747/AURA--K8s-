"""
Simple in-memory cache for ML predictions

LIMITATIONS:
- This is an in-memory cache and will not work in distributed deployments
- Cache is lost on service restart
- Not suitable for production multi-instance deployments

PRODUCTION ALTERNATIVES:
- Redis: Use redis-py for distributed caching
- Memcached: Use python-memcached for distributed caching
- External cache service: AWS ElastiCache, Google Cloud Memorystore, etc.

To use Redis in production:
1. Install: pip install redis
2. Replace PredictionCache with Redis-backed implementation
3. Configure REDIS_URL environment variable
"""

import os
import time
import hashlib
import json
from typing import Dict, Optional, Tuple, Any
from threading import Lock

# Cache configuration
CACHE_TTL_SECONDS = int(os.getenv("ML_PREDICTION_CACHE_TTL", "300"))  # 5 minutes default
CACHE_MAX_SIZE = int(os.getenv("ML_PREDICTION_CACHE_MAX_SIZE", "1000"))  # Max entries
CACHE_ENABLED = os.getenv("ML_PREDICTION_CACHE_ENABLED", "true").lower() == "true"


class PredictionCache:
    """Thread-safe in-memory cache for ML predictions"""
    
    def __init__(self, ttl_seconds: int = 300, max_size: int = 1000):
        self.cache: Dict[str, Tuple[Any, float]] = {}
        self.lock = Lock()
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self.hits = 0
        self.misses = 0
    
    def _generate_key(self, features: Dict[str, float]) -> str:
        """Generate cache key from feature dictionary"""
        # Sort features to ensure consistent keys
        sorted_features = sorted(features.items())
        features_str = json.dumps(sorted_features, sort_keys=True)
        return hashlib.sha256(features_str.encode()).hexdigest()
    
    def _is_expired(self, timestamp: float) -> bool:
        """Check if cache entry is expired"""
        return time.time() - timestamp > self.ttl_seconds
    
    def get(self, features: Dict[str, float]) -> Optional[Dict[str, Any]]:
        """Get prediction from cache if available and not expired"""
        if not CACHE_ENABLED:
            return None
        
        key = self._generate_key(features)
        
        with self.lock:
            if key in self.cache:
                prediction, timestamp = self.cache[key]
                if not self._is_expired(timestamp):
                    self.hits += 1
                    return prediction
                else:
                    # Remove expired entry
                    del self.cache[key]
            
            self.misses += 1
            return None
    
    def set(self, features: Dict[str, float], prediction: Dict[str, Any]) -> None:
        """Store prediction in cache"""
        if not CACHE_ENABLED:
            return
        
        key = self._generate_key(features)
        
        with self.lock:
            # Evict oldest entries if cache is full
            if len(self.cache) >= self.max_size:
                # Remove 10% of oldest entries
                entries_to_remove = max(1, self.max_size // 10)
                sorted_entries = sorted(self.cache.items(), key=lambda x: x[1][1])
                for i in range(entries_to_remove):
                    del self.cache[sorted_entries[i][0]]
            
            self.cache[key] = (prediction, time.time())
    
    def clear(self) -> None:
        """Clear all cache entries"""
        with self.lock:
            self.cache.clear()
            self.hits = 0
            self.misses = 0
    
    def stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        with self.lock:
            total_requests = self.hits + self.misses
            hit_rate = self.hits / total_requests if total_requests > 0 else 0.0
            
            return {
                "enabled": CACHE_ENABLED,
                "size": len(self.cache),
                "max_size": self.max_size,
                "ttl_seconds": self.ttl_seconds,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": hit_rate,
                "total_requests": total_requests
            }


# Global cache instance
prediction_cache = PredictionCache(ttl_seconds=CACHE_TTL_SECONDS, max_size=CACHE_MAX_SIZE)

