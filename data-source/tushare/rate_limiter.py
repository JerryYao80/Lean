"""
令牌桶限流器 - 严格控制 API 请求频率
"""
import threading
import time
from typing import Optional

import config


class TokenBucket:
    """
    线程安全的令牌桶限流器
    
    用于控制 API 请求频率，确保不超过每分钟最大请求数 (RPM)
    """
    
    def __init__(
        self,
        capacity: Optional[int] = None,
        refill_rate: Optional[float] = None
    ):
        """
        初始化令牌桶
        
        Args:
            capacity: 桶容量（最大令牌数）
            refill_rate: 令牌补充速率（每秒补充的令牌数）
        """
        self.capacity = capacity or config.TOKEN_BUCKET_CAPACITY
        self.refill_rate = refill_rate or config.TOKEN_REFILL_RATE
        self.tokens = float(self.capacity)
        self.last_refill_time = time.monotonic()
        self.lock = threading.Lock()
        
    def _refill(self) -> None:
        """补充令牌"""
        now = time.monotonic()
        elapsed = now - self.last_refill_time
        tokens_to_add = elapsed * self.refill_rate
        self.tokens = min(self.capacity, self.tokens + tokens_to_add)
        self.last_refill_time = now
        
    def acquire(self, tokens: int = 1, blocking: bool = True, timeout: Optional[float] = None) -> bool:
        """
        获取令牌
        
        Args:
            tokens: 需要获取的令牌数
            blocking: 是否阻塞等待
            timeout: 超时时间（秒），None 表示无限等待
            
        Returns:
            是否成功获取令牌
        """
        start_time = time.monotonic()
        
        while True:
            with self.lock:
                self._refill()
                
                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return True
                    
                if not blocking:
                    return False
                    
                # 计算需要等待的时间
                tokens_needed = tokens - self.tokens
                wait_time = tokens_needed / self.refill_rate
                
            # 检查超时
            if timeout is not None:
                elapsed = time.monotonic() - start_time
                if elapsed >= timeout:
                    return False
                wait_time = min(wait_time, timeout - elapsed)
                
            # 等待令牌补充
            time.sleep(min(wait_time, 0.1))  # 最多等待 0.1 秒后重试
            
    def get_wait_time(self, tokens: int = 1) -> float:
        """
        获取需要等待的时间
        
        Args:
            tokens: 需要获取的令牌数
            
        Returns:
            等待时间（秒）
        """
        with self.lock:
            self._refill()
            if self.tokens >= tokens:
                return 0.0
            tokens_needed = tokens - self.tokens
            return tokens_needed / self.refill_rate
            
    @property
    def available_tokens(self) -> float:
        """获取当前可用令牌数"""
        with self.lock:
            self._refill()
            return self.tokens


class RateLimiter:
    """
    API 请求限流器
    
    封装令牌桶，提供更友好的 API
    """
    
    def __init__(self, rpm: Optional[int] = None):
        """
        初始化限流器
        
        Args:
            rpm: 每分钟最大请求数
        """
        rpm = rpm or config.MAX_REQUESTS_PER_MINUTE
        self.bucket = TokenBucket(
            capacity=rpm,
            refill_rate=rpm / 60.0
        )
        self.request_count = 0
        self.start_time = time.time()
        self.lock = threading.Lock()
        
    def wait_for_token(self) -> None:
        """等待获取令牌（阻塞）"""
        self.bucket.acquire(blocking=True)
        with self.lock:
            self.request_count += 1
            
    def try_acquire(self, timeout: float = 30.0) -> bool:
        """
        尝试获取令牌
        
        Args:
            timeout: 超时时间（秒）
            
        Returns:
            是否成功获取
        """
        success = self.bucket.acquire(blocking=True, timeout=timeout)
        if success:
            with self.lock:
                self.request_count += 1
        return success
        
    def get_stats(self) -> dict:
        """获取统计信息"""
        with self.lock:
            elapsed = time.time() - self.start_time
            return {
                "total_requests": self.request_count,
                "elapsed_seconds": elapsed,
                "requests_per_minute": (self.request_count / elapsed * 60) if elapsed > 0 else 0,
                "available_tokens": self.bucket.available_tokens
            }


# 全局限流器实例
_global_limiter: Optional[RateLimiter] = None
_limiter_lock = threading.Lock()


def get_rate_limiter() -> RateLimiter:
    """获取全局限流器实例（单例）"""
    global _global_limiter
    if _global_limiter is None:
        with _limiter_lock:
            if _global_limiter is None:
                _global_limiter = RateLimiter()
    return _global_limiter


def reset_rate_limiter() -> None:
    """重置全局限流器"""
    global _global_limiter
    with _limiter_lock:
        _global_limiter = None
