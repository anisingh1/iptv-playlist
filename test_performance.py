#!/usr/bin/env python3
"""
Performance testing script for IPTV YouTube Proxy
Tests cache performance and stream resolution speed
"""

import time
import requests
import json
import sys

SERVER_URL = "http://192.168.1.16:6095"
TEST_CHANNELS = [
    "https://www.youtube.com/@aajtak/live",
    "https://www.youtube.com/@ABPNews/live",
    "https://www.youtube.com/@ndtvindia/live"
]

def test_health():
    """Test if server is running"""
    print("🔍 Testing server health...")
    try:
        response = requests.get(f"{SERVER_URL}/health", timeout=5)
        if response.status_code == 200:
            print("✅ Server is running")
            return True
        else:
            print(f"❌ Server returned status {response.status_code}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"❌ Server is not reachable: {e}")
        return False

def test_cache_status():
    """Test cache status"""
    print("\n🔍 Checking cache status...")
    try:
        response = requests.get(f"{SERVER_URL}/cache/status", timeout=5)
        if response.status_code == 200:
            data = response.json()
            total = data.get('total_cached', 0)
            ttl = data.get('cache_ttl_seconds', 0)
            
            print(f"✅ Cache Status:")
            print(f"   - Total cached: {total}")
            print(f"   - Cache TTL: {ttl} seconds ({ttl//60} minutes)")
            
            # Count valid entries
            valid = sum(1 for e in data.get('entries', []) if e.get('is_valid', False))
            print(f"   - Valid entries: {valid}/{total}")
            
            return total > 0
        else:
            print(f"❌ Failed to get cache status: {response.status_code}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"❌ Error getting cache status: {e}")
        return False

def test_stream_speed(url, test_name="Stream"):
    """Test how fast a stream request responds"""
    print(f"\n🔍 Testing {test_name}...")
    print(f"   URL: {url}")
    
    stream_url = f"{SERVER_URL}/stream?url={requests.utils.quote(url)}"
    
    try:
        start_time = time.time()
        response = requests.get(stream_url, timeout=10, stream=True)
        
        # Read first chunk
        first_chunk_time = None
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                first_chunk_time = time.time()
                break
        
        if first_chunk_time:
            elapsed = first_chunk_time - start_time
            print(f"✅ Time to first byte: {elapsed:.2f} seconds")
            
            if elapsed < 1.0:
                print(f"   🚀 EXCELLENT - Under 1 second!")
            elif elapsed < 2.0:
                print(f"   ✅ GOOD - Under 2 seconds")
            elif elapsed < 3.0:
                print(f"   ⚠️  OK - Could be better")
            else:
                print(f"   ❌ SLOW - Needs optimization")
                
            return elapsed
        else:
            print(f"❌ No data received")
            return None
            
    except requests.exceptions.Timeout:
        print(f"❌ Request timed out (>10 seconds)")
        return None
    except requests.exceptions.RequestException as e:
        print(f"❌ Error: {e}")
        return None

def run_performance_test():
    """Run complete performance test"""
    print("=" * 60)
    print("IPTV YouTube Proxy - Performance Test")
    print("=" * 60)
    
    # Test 1: Health check
    if not test_health():
        print("\n❌ Server is not running. Start it first with:")
        print("   python3 youtube-live.py")
        sys.exit(1)
    
    # Test 2: Cache status
    cache_ok = test_cache_status()
    if not cache_ok:
        print("\n⚠️  Cache is empty. Server might still be warming up.")
        print("   Wait 2-3 minutes and run this test again.")
    
    # Test 3: Stream speed tests
    print("\n" + "=" * 60)
    print("Stream Speed Tests")
    print("=" * 60)
    
    times = []
    for i, channel_url in enumerate(TEST_CHANNELS, 1):
        result = test_stream_speed(channel_url, f"Channel {i}")
        if result:
            times.append(result)
        time.sleep(1)  # Small delay between tests
    
    # Summary
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    
    if times:
        avg_time = sum(times) / len(times)
        min_time = min(times)
        max_time = max(times)
        
        print(f"\n📊 Results from {len(times)} tests:")
        print(f"   - Average time: {avg_time:.2f} seconds")
        print(f"   - Fastest: {min_time:.2f} seconds")
        print(f"   - Slowest: {max_time:.2f} seconds")
        
        print("\n💡 Performance Rating:")
        if avg_time < 1.0:
            print("   🌟🌟🌟 EXCELLENT - Optimizations working great!")
        elif avg_time < 2.0:
            print("   ✅✅ GOOD - Performance is solid")
        elif avg_time < 3.0:
            print("   ⚠️  OK - Room for improvement")
        else:
            print("   ❌ NEEDS WORK - Check optimization guide")
            
        print("\n📚 For more details, see:")
        print("   - QUICKSTART.md - Setup guide")
        print("   - DELAY_ANALYSIS.md - Performance analysis")
        print("   - PERFORMANCE_OPTIMIZATION.md - Advanced tips")
    else:
        print("\n❌ No successful tests. Check:")
        print("   1. Is the server running?")
        print("   2. Is yt-dlp installed?")
        print("   3. Can you access YouTube?")
        print("   4. Check server logs for errors")
    
    print("\n" + "=" * 60)

if __name__ == "__main__":
    try:
        run_performance_test()
    except KeyboardInterrupt:
        print("\n\n⚠️  Test interrupted by user")
        sys.exit(0)
